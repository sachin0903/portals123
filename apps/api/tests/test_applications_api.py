"""HTTP-layer tests for the journey endpoints (needs Docker).

The orchestrator's behaviour is covered elsewhere; these drive the *router* coroutines, which is
where the audit's dead ends actually lived:

* the staff step-fulfilment surface (``GET``/``POST .../steps/{key}/form``) shipped with **no test
  at all** — one request-level test would have caught the UI posting `{}` and the API happily
  storing an empty ``form_responses`` row, fulfilling the item and completing the step;
* nothing enforced that the step is assigned to STAFF, so any ``cases:write`` holder could sign off
  the client's own "Review your details" confirmation;
* the fanned-participant 422 guard on ``/action`` was orchestrator-tested only, and ``/retry`` had
  no guard at all — one call reset EVERY participant's instance of a fanned step;
* ``/cancel`` and ``/retry`` were tested only below the HTTP layer (no 422 mapping, no permission
  gate).

Each test calls the endpoint coroutine with a real ``FirmContext`` (service_role session + the
caller's resolved tenant), exactly as ``firm_session`` would hand it over.
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue, storage, tenant_storage
from app.auth import Principal
from app.db import FirmContext
from app.flows import orchestrator
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from app.permissions import CASES_READ, CASES_WRITE, CONTACTS_READ_ALL
from app.routers import applications as api
from app.tasks import service as tasks_service
from tests.conftest import SECCL_PLATFORM_SQL


def _principal(*perms: Any, scoped: bool = False) -> Principal:
    """A staff caller for a fixture journey.

    The book gate treats ``contacts:read_all`` as the whole firm. These tests open a journey
    they are allowed to touch, so that permission is on unless ``scoped`` — the tests that
    prove an adviser does not see someone else's journey.
    """
    slugs = [p.slug if hasattr(p, "slug") else str(p) for p in perms]
    if not scoped and CONTACTS_READ_ALL.slug not in slugs:
        slugs.append(CONTACTS_READ_ALL.slug)
    return Principal(
        user_id="wos_staff",
        organisation_id="org_1",
        role="adviser",
        permissions=slugs,
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


def _definition(
    assignee: str, form_placeholder: str = "__FORM_UID__", *, artifacts: bool = False
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {
            "id": "core",
            "type": "fill_form",
            "title": "Applicant details",
            "scope": "participant",
            "assignee": assignee,
            "config": {
                "form_uid": form_placeholder,
                "mapping": [{"source": "first_name", "target": "first_name"}],
            },
        },
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "assignee": "staff",
            "config": {},
        },
    ]
    if artifacts:
        # The other two request-backed types, so the bypass probe can be run against every step
        # whose completion IS the artifact — not just the form.
        steps += [
            {
                "id": "terms",
                "type": "accept_terms",
                "title": "Accept the terms",
                "scope": "participant",
                "assignee": "client",
                "config": {"document_uid": "doc_missing", "title": "Accept the terms"},
            },
            {
                "id": "proof",
                "type": "upload_document",
                "title": "Proof of address",
                "scope": "participant",
                "assignee": "client",
                "config": {"title": "Proof of address"},
            },
            # An application-scoped form has no contact to bind a request to, so it instantiates
            # BLOCKED with no request item at all — the shape a `retry` also leaves behind. Its
            # request_item_id is NULL, which is the other way a step can have no artifact.
            {
                "id": "orphan",
                "type": "fill_form",
                "title": "Nobody's form",
                "scope": "application",
                "assignee": "staff",
                "config": {"form_uid": form_placeholder},
            },
        ]
    return {"steps": steps, "milestones": []}


class _Fx:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _seed(
    sess: AsyncSession,
    *,
    assignee: str = "staff",
    participants: int = 1,
    required: bool = True,
    artifacts: bool = False,
    steps: list[dict[str, Any]] | None = None,
    flow_code: str | None = None,
    milestones: list[dict[str, Any]] | None = None,
) -> _Fx:
    """A tenant + application + `participants` owners + a running one-form flow.

    ``steps`` overrides ``_definition(assignee, artifacts=artifacts)`` outright — for tests that
    need a specific step_key sequence (e.g. the onboarding stage rollup) rather than the default
    core/idv pair. It still goes through the same ``__FORM_UID__`` substitution, so a custom
    ``fill_form`` step can reference the one seeded form. ``milestones`` replaces the empty list
    on that definition, or the milestones of ``_definition`` when ``steps`` is omitted."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"ap_{s}",
            a=f"ap-{s}",
            n=f"AP {s}",
        )
    )
    platform = str(
        await _scalar(
            sess,
            SECCL_PLATFORM_SQL,
            n=f"Seccl {s}",
        )
    )
    app_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
            "VALUES (:t, :p, :u, 'draft') RETURNING id",
            t=tenant,
            p=platform,
            u=f"app_{s}",
        )
    )
    contacts: list[int] = []
    for i in range(participants):
        contacts.append(
            int(
                await _scalar(
                    sess,
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
                    "VALUES (:t, :u, 'individual', :e) RETURNING id",
                    t=tenant,
                    u=f"c{i}_{s}",
                    e=f"c{i}_{s}@t.test",
                )
            )
        )
    account = int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
            "VALUES (:t, :c, :a, 'draft', :u) RETURNING id",
            t=tenant,
            c=contacts[0],
            a=app_id,
            u=f"acc_{s}",
        )
    )
    for cid in contacts:
        await sess.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
            ),
            {"t": tenant, "a": app_id, "acc": account, "c": cid},
        )
    form_uid = f"frm_{s}"
    form_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.forms (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Fact find', 'published') RETURNING id",
            u=form_uid,
            t=tenant,
            c=f"ff_{s}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO documents.form_fields (uid, tenant_id, form_id, field_key, field_type, "
            "  label, required, sort_order) "
            "VALUES (:u, :t, :f, 'first_name', 'text', 'First name', :req, 1)"
        ),
        {"u": f"fld_{s}", "t": tenant, "f": form_id, "req": required},
    )
    raw_definition = (
        {"steps": steps, "milestones": milestones or []}
        if steps is not None
        else _definition(assignee, artifacts=artifacts)
    )
    if milestones is not None and steps is None:
        raw_definition = {**raw_definition, "milestones": milestones}
    definition = json.loads(json.dumps(raw_definition).replace("__FORM_UID__", form_uid))
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :code, 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
            code=flow_code or f"onb_{s}",
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{s}",
            t=tenant,
            f=flow_id,
            d=json.dumps(definition),
        )
    )
    created = await orchestrator.start_execution(
        sess,
        tenant_id=tenant,
        flow_id=flow_id,
        flow_version_id=version_id,
        definition=definition,
        graph=ApplicationGraph(
            participants=tuple(
                Participant(cid, "individual", frozenset({"owner"})) for cid in contacts
            ),
            accounts=(AccountSpec(account, tuple(contacts)),),
        ),
        application_id=app_id,
        data={},
    )
    case_uid = str(
        await _scalar(
            sess,
            "SELECT c.uid FROM flows.cases c WHERE c.execution_id = :e AND c.subject_id = :s "
            "  AND c.subject_type = 'contact'",
            e=int(created["id"]),
            s=contacts[0],
        )
    )
    return _Fx(
        tenant=tenant,
        contacts=contacts,
        contact=contacts[0],
        exec_uid=str(created["uid"]),
        execution_id=int(created["id"]),
        case_uid=case_uid,
        form_uid=form_uid,
        application_id=app_id,
        account=account,
    )


def _ctx(sess: AsyncSession, fx: _Fx) -> FirmContext:
    return FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal(CASES_WRITE))


async def _step_status(sess: AsyncSession, fx: _Fx, key: str, participant: int | None) -> str:
    return str(
        await _scalar(
            sess,
            "SELECT cs.status FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = :k "
            "  AND cs.participant_subject_id IS NOT DISTINCT FROM :p",
            e=fx.execution_id,
            k=key,
            p=participant,
        )
    )


@pytest.mark.asyncio
async def test_list_applications_names_the_responsible_adviser(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    suffix = uuid.uuid4().hex[:8]
    adviser_id = await _scalar(
        sess,
        "INSERT INTO auth.users "
        "  (uid, tenant_id, email, workos_user_id, first_name, last_name, enabled) "
        "VALUES (:uid, :t, :email, :workos, 'Alex', 'Morgan', true) RETURNING id",
        uid=f"adv_{suffix}",
        t=fx.tenant,
        email=f"adviser_{suffix}@t.test",
        workos=f"wos_adv_{suffix}",
    )
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:adviser AS uuid) "
            "WHERE id = ("
            "  SELECT crm_application_id FROM flows.executions WHERE id = :execution"
            ")"
        ),
        {"adviser": adviser_id, "execution": fx.execution_id},
    )

    rows = await api.list_applications(
        principal=_principal(CASES_READ, CONTACTS_READ_ALL),
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal(CASES_READ)),
    )

    row = next(item for item in rows if item["uid"] == fx.exec_uid)
    assert row["adviser_name"] == "Alex Morgan"


@pytest.mark.asyncio
async def test_list_applications_is_the_callers_book_without_read_all(
    service_session: AsyncSession,
) -> None:
    """An adviser sees the journeys for their clients, or that they are otherwise on — assigned,
    or the adviser of record. Not the rest of the firm's. ``contacts:read_all`` lifts that, the
    same way it does for the clients list. ``owner_is_me`` names the caller's own move."""
    sess = service_session
    fx = await _seed(sess)
    me = await _platform_member(sess, fx.tenant, "Mina", "Cole")
    other = await _platform_member(sess, fx.tenant, "Owen", "Dale")
    # Somebody else's journey outright: Owen assigned, Owen the adviser, Owen's client.
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": other, "e": fx.execution_id},
    )
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:u AS uuid) WHERE id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)"
        ),
        {"u": other, "e": fx.execution_id},
    )
    await sess.execute(
        text(
            "SELECT set_config('request.jwt.claims', "
            "jsonb_build_object('user_uuid', CAST(:u AS text))::text, true)"
        ),
        {"u": me},
    )

    async def visible(*perms: Any) -> dict[str, dict[str, Any]]:
        rows = await api.list_applications(
            principal=_principal(CASES_READ, *perms, scoped=True),
            ctx=FirmContext(
                session=sess, tenant_id=fx.tenant, principal=_principal(CASES_READ, scoped=True)
            ),
        )
        return {str(item["uid"]): dict(item) for item in rows}

    assert fx.exec_uid not in await visible()
    # Reading the whole firm shows it, and says whose move it is: not mine.
    everything = await visible(CONTACTS_READ_ALL)
    assert fx.exec_uid in everything
    assert everything[fx.exec_uid]["owner_is_me"] is False

    # The client joins my team: visible, though the ball is still Owen's.
    await sess.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:u AS uuid), 'paraplanner')"
        ),
        {"t": fx.tenant, "c": fx.contact, "u": me},
    )
    mine = await visible()
    assert fx.exec_uid in mine
    assert mine[fx.exec_uid]["owner_is_me"] is False
    await sess.execute(
        text("DELETE FROM crm.client_advisers WHERE contact_id = :c"), {"c": fx.contact}
    )
    assert fx.exec_uid not in await visible()

    # Assigned to me: visible, and my move.
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": me, "e": fx.execution_id},
    )
    assert (await visible())[fx.exec_uid]["owner_is_me"] is True

    # Unassigned with me as the adviser: visible, and my move by default.
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = NULL WHERE id = :e"),
        {"e": fx.execution_id},
    )
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:u AS uuid) WHERE id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)"
        ),
        {"u": me, "e": fx.execution_id},
    )
    assert (await visible())[fx.exec_uid]["owner_is_me"] is True


@pytest.mark.asyncio
async def test_list_applications_mine_is_only_the_callers(
    service_session: AsyncSession,
) -> None:
    """``mine`` is the dashboard's "my journeys": assigned to the caller, or unassigned and theirs
    as the adviser. It compares against auth.uid() — the internal user id both columns store —
    because the portal knows the caller only by their WorkOS id and cannot make the comparison
    itself. Everyone else's journeys stay out."""
    sess = service_session
    fx = await _seed(sess)
    me = await _platform_member(sess, fx.tenant, "Mina", "Cole")
    other = await _platform_member(sess, fx.tenant, "Owen", "Dale")
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": other, "e": fx.execution_id},
    )
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:u AS uuid) WHERE id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)"
        ),
        {"u": other, "e": fx.execution_id},
    )

    # The claims the request layer mints: auth.uid() reads `user_uuid`. Local to this transaction,
    # which is the one the fixture opened and the query below runs in.
    await sess.execute(
        text(
            "SELECT set_config('request.jwt.claims', "
            "jsonb_build_object('user_uuid', CAST(:u AS text))::text, true)"
        ),
        {"u": me},
    )

    async def mine_uids() -> set[str]:
        rows = await api.list_applications(
            principal=_principal(CASES_READ, scoped=True),
            ctx=FirmContext(
                session=sess, tenant_id=fx.tenant, principal=_principal(CASES_READ, scoped=True)
            ),
            mine=True,
        )
        return {item["uid"] for item in rows}

    # Somebody else's, assigned and advised: out.
    assert fx.exec_uid not in await mine_uids()

    # Assigned to me: in, whoever the adviser is.
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": me, "e": fx.execution_id},
    )
    assert fx.exec_uid in await mine_uids()

    # Unassigned: the adviser's. Theirs while that is somebody else, mine once it is me.
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = NULL WHERE id = :e"),
        {"e": fx.execution_id},
    )
    assert fx.exec_uid not in await mine_uids()
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:u AS uuid) WHERE id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)"
        ),
        {"u": me, "e": fx.execution_id},
    )
    assert fx.exec_uid in await mine_uids()


@pytest.mark.asyncio
async def test_a_journey_outside_the_book_cannot_be_opened_or_cancelled(
    service_session: AsyncSession,
) -> None:
    """The list is not the only door. Detail and cancel use the same book rule, and a miss is
    404 — the uid is not an oracle. ``contacts:read_all`` lifts it, as it does for the list."""
    sess = service_session
    fx = await _seed(sess)
    me = await _platform_member(sess, fx.tenant, "Mina", "Cole")
    other = await _platform_member(sess, fx.tenant, "Owen", "Dale")
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": other, "e": fx.execution_id},
    )
    await sess.execute(
        text(
            "UPDATE crm.applications SET adviser_id = CAST(:u AS uuid) WHERE id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)"
        ),
        {"u": other, "e": fx.execution_id},
    )
    await sess.execute(
        text(
            "SELECT set_config('request.jwt.claims', "
            "jsonb_build_object('user_uuid', CAST(:u AS text))::text, true)"
        ),
        {"u": me},
    )
    ctx = FirmContext(
        session=sess, tenant_id=fx.tenant, principal=_principal(CASES_READ, scoped=True)
    )
    scoped = _principal(CASES_READ, CASES_WRITE, scoped=True)
    with pytest.raises(HTTPException) as opened:
        await api.get_application(fx.exec_uid, principal=scoped, ctx=ctx)
    assert opened.value.status_code == 404
    with pytest.raises(HTTPException) as cancelled:
        await api.cancel_application(fx.exec_uid, api.CancelBody(), principal=scoped, ctx=ctx)
    assert cancelled.value.status_code == 404

    whole = _principal(CASES_READ, CASES_WRITE, CONTACTS_READ_ALL)
    detail = await api.get_application(fx.exec_uid, principal=whole, ctx=ctx)
    assert detail["uid"] == fx.exec_uid


async def _platform_member(sess: AsyncSession, tenant: int, first: str, last: str) -> str:
    s = uuid.uuid4().hex[:8]
    user_id = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
            "  last_name, enabled) "
            "VALUES (:uid, :t, :e, :w, :f, :last, true) RETURNING id",
            uid=f"usr_{s}",
            t=tenant,
            e=f"{first.lower()}_{s}@t.test",
            w=f"wos_{s}",
            f=first,
            last=last,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
            "VALUES (:u, :t, 'platform', 'active')"
        ),
        {"u": user_id, "t": tenant},
    )
    return user_id


@pytest.mark.asyncio
async def test_the_journey_names_its_adviser_and_assignee(service_session: AsyncSession) -> None:
    """The detail page has to show who advises the client and who the staff work belongs to.
    Those are different people: the adviser was chosen at kickoff, the assignee is any
    platform member."""
    sess = service_session
    fx = await _seed(sess)
    adviser_id = await _platform_member(sess, fx.tenant, "Alex", "Morgan")
    await sess.execute(
        text("UPDATE crm.applications SET adviser_id = CAST(:adviser AS uuid) WHERE id = :app"),
        {"adviser": adviser_id, "app": fx.application_id},
    )
    assignee_id = await _platform_member(sess, fx.tenant, "Priya", "Shah")

    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    assert out["adviser_name"] == "Alex Morgan"
    # This seed's flow is not the onboarding flow, so a completed form on it never reopens.
    assert out["forms_amendable"] is False
    assert out["assignee_name"] is None

    role_id = int(
        await _scalar(
            sess,
            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
            "VALUES (:t, :slug, 'Operations', 'platform') RETURNING id",
            t=fx.tenant,
            slug=f"ops_{uuid.uuid4().hex[:6]}",
        )
    )
    await tasks_service.create_task(
        sess,
        tenant_id=fx.tenant,
        assignee_kind="role",
        assignee_role_id=role_id,
        title="Open the account",
        subject_type="execution",
        subject_id=fx.exec_uid,
        source_kind="flow",
        source_ref=f"staff-{fx.exec_uid}",
    )
    await tasks_service.create_task(
        sess,
        tenant_id=fx.tenant,
        assignee_kind="contact",
        assignee_contact_id=fx.contact,
        title="Sign the declaration",
        subject_type="execution",
        subject_id=fx.exec_uid,
        source_kind="flow",
        source_ref=f"client-{fx.exec_uid}",
    )

    assigned = await api.assign_application(
        exec_uid=fx.exec_uid,
        body=api.AssignBody(user_id=assignee_id),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert assigned["tasks_moved"] == 1
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    assert out["assignee_name"] == "Priya Shah"
    staff_task = (
        (
            await sess.execute(
                text(
                    "SELECT assignee_kind, assignee_user_id::text AS assignee_user_id "
                    "FROM tasks.tasks WHERE source_ref = :r"
                ),
                {"r": f"staff-{fx.exec_uid}"},
            )
        )
        .mappings()
        .one()
    )
    assert staff_task["assignee_kind"] == "user"
    assert staff_task["assignee_user_id"] == assignee_id
    client_task = (
        (
            await sess.execute(
                text(
                    "SELECT assignee_kind, assignee_contact_id "
                    "FROM tasks.tasks WHERE source_ref = :r"
                ),
                {"r": f"client-{fx.exec_uid}"},
            )
        )
        .mappings()
        .one()
    )
    assert client_task["assignee_kind"] == "contact"
    assert client_task["assignee_contact_id"] == fx.contact

    strangers = await api.list_application_assignees(
        principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    assert {row["name"] for row in strangers} >= {"Alex Morgan", "Priya Shah"}

    with pytest.raises(HTTPException) as refused:
        await api.assign_application(
            exec_uid=fx.exec_uid,
            body=api.AssignBody(user_id=str(uuid.uuid4())),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert refused.value.status_code == 422


# ==================================================================================================
# the staff step-fulfilment surface
# ==================================================================================================
def test_employer_prefill_carries_the_client_record_into_the_journey() -> None:
    values = api._employer_form_prefill(
        {
            "employer_name": "Acme Ltd",
            "employer_registration_number": "12345678",
            "employer_address": {"line1": "1 High Street", "city": "Leeds", "postcode": "LS1 1AA"},
            "employer_contributions": False,
        },
        {
            "pension": {
                "employerContributions": {"accountNumber": "12345678", "sortCode": "20-30-40"}
            }
        },
    )
    assert values["pension.hasEmployerContributions"] is True
    assert values["pension.employerContributions.name"] == "Acme Ltd"
    assert values["pension.employerContributions.registrationNumber"] == "12345678"
    assert values["employer_address"]["city"] == "Leeds"
    assert values["pension.employerContributions.sortCode"] == "20-30-40"


def test_illustration_contact_gaps_fill_blanks_and_leave_crm_values() -> None:
    gaps = api._illustration_contact_gaps(
        {
            "exit_age": 67,
            "marital_status": "married",
            "source_of_wealth": "Salary",
            "bank_account": {"sort_code": "20-30-40", "account_number": "12345678"},
        },
        {"pension.retirementAge": 60, "source_of_wealth": "Inheritance"},
    )
    assert "pension.retirementAge" not in gaps
    assert "source_of_wealth" not in gaps
    assert gaps["pension.maritalStatus"] == "married"
    assert gaps["bank_account.sort_code"] == "20-30-40"
    assert api._illustration_contact_gaps({"exit_age": 65}, {}) == {"pension.retirementAge": 65}


def test_illustration_prefill_maps_only_equivalent_onboarding_answers() -> None:
    values = api._illustration_account_prefill(
        {
            "model_id": 42,
            "quotation_type": "Flexi Access Drawdown",
            "drawdown_type": "UFPLS",
            "existing_fund_value": 100_000,
            "initial_payment": 8_000,
            "transfer_value": 25_000,
            "employer_contribution": 500,
            "employer_contribution_frequency": "Half Yearly",
            "withdrawal_amount": 750,
            "withdrawal_frequency": "Monthly",
            "withdrawal_start_date": "2027-04-01",
            "ongoing_adviser_fee_percent": 0.75,
        }
    )
    assert values == {
        "model_id": "42",
        "crystallisation_status": "drawdown",
        "access_option": "ufpls",
        "has_initial_cash": True,
        "initial_cash_amount": 8_000,
        "has_transfer": True,
        "transfer_amount": 25_000,
        "has_contribution": True,
        "contribution_amount": 500,
        "contribution_frequency": "half_yearly",
        "has_regular_withdrawal": True,
        "withdrawal_amount": 750,
        "withdrawal_frequency": "monthly",
        "withdrawal_start_date": "2027-04-01",
    }


def test_illustration_prefill_leaves_ambiguous_or_unsupported_answers_blank() -> None:
    values = api._illustration_account_prefill(
        {
            "member_contribution": 100,
            "employer_contribution": 200,
            "withdrawal_amount": 50,
            "withdrawal_frequency": "2 Months",
        }
    )
    assert values == {"has_regular_withdrawal": True, "withdrawal_amount": 50}


def test_illustration_prefill_does_not_invent_cash_for_an_existing_account() -> None:
    values = api._illustration_account_prefill({"account_id": 9, "initial_payment": 80_000})
    assert "has_initial_cash" not in values


def test_illustration_prefill_does_not_treat_other_sipps_as_cash_on_the_new_plan() -> None:
    values = api._illustration_account_prefill({"existing_fund_value": 80_000})
    assert "has_initial_cash" not in values


def test_contact_prefill_uses_the_book_slug() -> None:
    # The field stores the slug. An older fact-find stored the display label, and that
    # still resolves onto the slug the field offers now.
    assert api._form_choice("single", ["single", "married", "divorced", "widowed"]) == "single"
    assert api._form_choice("Single", ["single", "married", "divorced", "widowed"]) == "single"
    assert api._form_choice("self_employed", ["employed", "self_employed"]) == "self_employed"
    assert api._form_choice("Self-Employed", ["employed", "self_employed"]) == "self_employed"
    assert api._form_choice("civil_partner", ["single", "married"]) is None


@pytest.mark.asyncio
async def test_get_step_form_returns_the_forms_fields(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    out = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert out.form_uid == fx.form_uid
    assert [f.field_key for f in out.fields] == ["first_name"]
    assert out.answers is None


@pytest.mark.asyncio
async def test_account_form_context_and_name_prefill_use_the_selected_product(
    service_session: AsyncSession,
) -> None:
    """The portal narrows the tenant's model catalogue by ``account.custodian``.

    This response called the same property ``provider``, so the portal read NULL and filtered out
    every provider-owned model. The model query itself is RLS-scoped to this firm; the context must
    carry the matching custodian code so its own models remain selectable.
    """
    fx = await _seed(
        service_session,
        steps=[
            {
                "id": "preferences",
                "type": "fill_form",
                "title": "Account preferences",
                "scope": "account",
                "assignee": "staff",
                "config": {"form_uid": "__FORM_UID__"},
            }
        ],
    )
    product = await catalogue.upsert_product(
        service_session,
        fx.tenant,
        {"product_type": "GIA", "name": "Sterling GIA", "allowed_currencies": ["GBP"]},
    )
    await service_session.execute(
        text("UPDATE platform.accounts SET product_id = :p WHERE id = :a"),
        {"p": int(product["id"]), "a": fx.account},
    )
    await service_session.execute(
        text("UPDATE crm.contacts SET first_name = 'Billy', surname = 'Kerr' WHERE id = :c"),
        {"c": fx.contact},
    )
    await service_session.execute(
        text(
            "INSERT INTO documents.form_fields "
            "  (uid, tenant_id, form_id, field_key, field_type, label, required, sort_order) "
            "SELECT :u, tenant_id, id, 'account_name', 'text', 'Account name', true, 2 "
            "FROM documents.forms WHERE uid = :form"
        ),
        {"u": f"name_{fx.form_uid}", "form": fx.form_uid},
    )
    case_uid = str(
        await _scalar(
            service_session,
            "SELECT c.uid FROM flows.cases c "
            "JOIN flows.executions e ON e.id = c.execution_id "
            "WHERE e.uid = :e AND c.subject_type = 'account'",
            e=fx.exec_uid,
        )
    )

    out = await api.get_step_form(
        fx.exec_uid,
        case_uid,
        "preferences",
        principal=_principal(CASES_READ),
        ctx=_ctx(service_session, fx),
    )

    assert out.context["account"]["custodian"] == "seccl"
    assert out.context["account"]["allowed_currencies"] == ["GBP"]
    assert out.answers is not None
    assert out.answers["account_name"] == "Billy Kerr GIA"
    assert "provider" not in out.context["account"]


@pytest.mark.asyncio
async def test_get_step_form_prefills_facts_already_held_on_the_client(
    service_session: AsyncSession,
) -> None:
    """Kickoff's client details are not typed for a second time in the first fact-find."""
    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text("UPDATE crm.contacts SET first_name = 'Ada' WHERE id = :c"),
        {"c": fx.contact},
    )

    out = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )

    assert out.answers == {"first_name": "Ada"}


@pytest.mark.asyncio
async def test_get_step_form_defaults_the_bank_account_name_from_the_person(
    service_session: AsyncSession,
) -> None:
    """A blank bank name is the person's name, never a stored one we would overwrite."""
    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text(
            "UPDATE crm.contacts SET first_name = 'Ada', middle_name = 'Augusta', "
            "surname = 'Byron' WHERE id = :c"
        ),
        {"c": fx.contact},
    )
    await sess.execute(
        text(
            "INSERT INTO documents.form_fields (uid, tenant_id, form_id, field_key, field_type, "
            "  label, required, sort_order) "
            "SELECT :u, f.tenant_id, f.id, 'bank_account.account_name', 'text', "
            "       'Account holder name', true, 2 "
            "FROM documents.forms f WHERE f.uid = :form"
        ),
        {"u": f"bnk_{fx.form_uid}", "form": fx.form_uid},
    )

    out = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert out.answers is not None
    assert out.answers["first_name"] == "Ada"
    assert out.answers["bank_account.account_name"] == "Ada Augusta Byron"

    await sess.execute(
        text(
            "UPDATE crm.contacts SET bank_account = "
            "  jsonb_build_object('account_name', 'Ada Lovelace') WHERE id = :c"
        ),
        {"c": fx.contact},
    )
    stored = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert stored.answers is not None
    assert stored.answers["bank_account.account_name"] == "Ada Lovelace"


@pytest.mark.asyncio
async def test_get_step_form_reopens_on_the_stored_bank_rail(
    service_session: AsyncSession,
) -> None:
    """An IBAN reopens on International; a Faster Payment reopens on that method, not BACS."""
    sess = service_session
    fx = await _seed(sess)
    for key, label, ftype in (
        ("bank_style", "Account type", "segmented"),
        ("bank_account.iban", "IBAN", "text"),
        ("bank_account.bic", "SWIFT/BIC", "text"),
        ("bank_account.method", "Payment method", "segmented"),
        ("bank_account.sort_code", "Sort code", "sortCode"),
    ):
        await sess.execute(
            text(
                "INSERT INTO documents.form_fields (uid, tenant_id, form_id, field_key, "
                "  field_type, label, required, sort_order) "
                "SELECT :u, f.tenant_id, f.id, :k, :ft, :l, true, 3 "
                "FROM documents.forms f WHERE f.uid = :form"
            ),
            {
                "u": f"rail_{key}_{fx.form_uid}",
                "k": key,
                "ft": ftype,
                "l": label,
                "form": fx.form_uid,
            },
        )
    await sess.execute(
        text("UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) WHERE id = :c"),
        {
            "c": fx.contact,
            "b": json.dumps(
                {"name": "Ada Lovelace", "iban": "GB82WEST12345698765432", "bic": "BARCGB22"}
            ),
        },
    )
    intl = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert intl.answers is not None
    assert intl.answers["bank_style"] == "international"
    assert intl.answers["bank_account.iban"] == "GB82WEST12345698765432"
    assert "bank_account.method" not in intl.answers

    await sess.execute(
        text("UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) WHERE id = :c"),
        {
            "c": fx.contact,
            "b": json.dumps(
                {
                    "name": "Ada Lovelace",
                    "sort_code": "12-34-56",
                    "account_number": "12345678",
                    "method": "faster_payment",
                }
            ),
        },
    )
    uk = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert uk.answers is not None
    assert uk.answers["bank_style"] == "uk"
    assert uk.answers["bank_account.method"] == "faster_payment"


@pytest.mark.asyncio
async def test_submitting_the_form_stores_the_answers_and_completes_the_step(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await api.submit_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    stored = await _scalar(
        sess,
        "SELECT fr.answers FROM documents.form_responses fr "
        "JOIN documents.request_items ri ON ri.id = fr.request_item_id "
        "JOIN flows.case_steps cs ON cs.request_item_id = ri.id "
        "JOIN flows.cases c ON c.id = cs.case_id "
        "WHERE c.execution_id = :e AND cs.step_key = 'core'",
        e=fx.execution_id,
    )
    answers = stored if isinstance(stored, dict) else json.loads(stored)
    assert answers == {"first_name": "Ada"}
    assert await _step_status(sess, fx, "core", fx.contact) == "fulfilled"


@pytest.mark.asyncio
async def test_an_empty_submission_is_refused_rather_than_silently_completing_the_step(
    service_session: AsyncSession,
) -> None:
    """The exact shape of the UI bug: the browser posted ``{}`` (a stale hidden input) and the API
    wrote an empty form_responses row, fulfilled the item and completed the fact-find."""
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            fx.exec_uid,
            fx.case_uid,
            "core",
            api.StepFormSubmit(answers={}, participant_contact_id=fx.contact),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert "First name" in str(exc.value.detail)
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.form_responses WHERE tenant_id = :t",
                t=fx.tenant,
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_a_blank_string_does_not_answer_a_required_question(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            fx.exec_uid,
            fx.case_uid,
            "core",
            api.StepFormSubmit(answers={"first_name": "   "}, participant_contact_id=fx.contact),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_an_optional_only_form_may_be_submitted_empty(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, required=False)
    await api.submit_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        api.StepFormSubmit(answers={}, participant_contact_id=fx.contact),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert await _step_status(sess, fx, "core", fx.contact) == "fulfilled"


@pytest.mark.asyncio
async def test_staff_cannot_complete_a_client_assigned_step_on_their_behalf(
    service_session: AsyncSession,
) -> None:
    """The seeded 'Review your details' is client-assigned: staff signing it off destroys the only
    evidence the client ever confirmed anything. The portal hid the action; nothing enforced it."""
    sess = service_session
    fx = await _seed(sess, assignee="client")
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            fx.exec_uid,
            fx.case_uid,
            "core",
            api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 403
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"


@pytest.mark.asyncio
async def test_a_completed_onboarding_form_can_be_amended_until_declaration(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, flow_code="onboarding")
    body = api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact)
    await api.submit_step_form(
        fx.exec_uid, fx.case_uid, "core", body, _principal(CASES_WRITE), _ctx(sess, fx)
    )
    amended = api.StepFormSubmit(answers={"first_name": "Grace"}, participant_contact_id=fx.contact)
    result = await api.submit_step_form(
        fx.exec_uid, fx.case_uid, "core", amended, _principal(CASES_WRITE), _ctx(sess, fx)
    )
    assert result["amended"] is True
    # The journey page offers Amend off this flag, so it has to agree with the submit that just
    # succeeded — and flip the moment the application is declared and sent.
    assert (await api.get_application(fx.exec_uid, _principal(CASES_READ), _ctx(sess, fx)))[
        "forms_amendable"
    ] is True
    assert (
        await _scalar(sess, "SELECT first_name FROM crm.contacts WHERE id = :c", c=fx.contact)
        == "Grace"
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.form_responses fr "
            "JOIN flows.case_steps cs ON cs.request_item_id = fr.request_item_id "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'core'",
            e=fx.execution_id,
        )
        == 2
    )

    # The boolean stamped at kickoff is not the lock. Journeys started before mid-September
    # 2026 have it true from the moment they were created, with Declare and send still waiting.
    await sess.execute(
        text("UPDATE crm.applications SET adviser_declaration = true WHERE id = :a"),
        {"a": fx.application_id},
    )
    assert (await api.get_application(fx.exec_uid, _principal(CASES_READ), _ctx(sess, fx)))[
        "forms_amendable"
    ] is True
    await sess.execute(
        text(
            "INSERT INTO flows.case_steps (tenant_id, case_id, step_id, step_key, step_type, "
            "  title, assignee, status) "
            "SELECT c.tenant_id, c.id, 'declaration', 'declaration', 'task', "
            "  'Declare and send to the client', 'staff', 'fulfilled' "
            "FROM flows.cases c WHERE c.execution_id = :e LIMIT 1"
        ),
        {"e": fx.execution_id},
    )
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            fx.exec_uid, fx.case_uid, "core", amended, _principal(CASES_WRITE), _ctx(sess, fx)
        )
    assert exc.value.status_code == 422
    assert "locked" in str(exc.value.detail)
    assert (await api.get_application(fx.exec_uid, _principal(CASES_READ), _ctx(sess, fx)))[
        "forms_amendable"
    ] is False


@pytest.mark.asyncio
async def test_account_preferences_refuse_a_currency_outside_the_product(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(
        sess,
        required=False,
        steps=[
            {
                "id": "preferences",
                "type": "fill_form",
                "title": "Account details",
                "scope": "account",
                "assignee": "staff",
                "config": {"form_uid": "__FORM_UID__", "mode": "account_preferences"},
            }
        ],
    )
    product = await catalogue.upsert_product(
        sess,
        fx.tenant,
        {
            "product_type": "GIA",
            "name": "Sterling GIA",
            "allowed_currencies": ["GBP"],
        },
    )
    await sess.execute(
        text("UPDATE platform.accounts SET product_id = :p WHERE id = :a"),
        {"p": int(product["id"]), "a": fx.account},
    )
    case_uid = str(
        await _scalar(
            sess,
            "SELECT uid FROM flows.cases WHERE subject_type = 'account' AND subject_id = :a",
            a=fx.account,
        )
    )

    result = await api.submit_step_form(
        fx.exec_uid,
        case_uid,
        "preferences",
        api.StepFormSubmit(
            answers={
                "account_name": "Growth account",
                "currency": "EUR",
                "investment_capacity": "advisory",
            }
        ),
        _principal(CASES_WRITE),
        _ctx(sess, fx),
    )

    assert result["blocked"] is True
    assert result["reason"] == "EUR is not allowed for this product."


@pytest.mark.asyncio
async def test_a_refused_write_back_is_reported_and_can_be_corrected_from_the_form(
    service_session: AsyncSession,
) -> None:
    """A handler that refuses its write-back leaves the step ``blocked`` with its item already
    ``fulfilled`` — and that shape could be submitted from NO door: the amendment branch needs a
    fulfilled step, the first-submission branch needs a pending item. So an adviser who picked a
    fee package the account cannot take got a 200, was moved on to the next form, and left a
    journey stuck behind them that could only be freed with SQL. Two things are pinned: the
    refusal comes back instead of a bare success, and the corrected answers go through.
    """
    sess = service_session
    fx = await _seed(
        sess,
        required=False,
        steps=[
            {
                "id": "preferences",
                "type": "fill_form",
                "title": "Account details",
                "scope": "account",
                "assignee": "staff",
                "config": {"form_uid": "__FORM_UID__", "mode": "account_preferences"},
            }
        ],
    )
    case_uid = str(
        await _scalar(
            sess,
            "SELECT c.uid FROM flows.cases c "
            "JOIN flows.executions e ON e.id = c.execution_id "
            "WHERE e.uid = :e AND c.subject_type = 'account'",
            e=fx.exec_uid,
        )
    )
    answers: dict[str, Any] = {
        "account_name": "Growth SIPP",
        "currency": "GBP",
        "investment_capacity": "advisory",
    }

    refused = await api.submit_step_form(
        fx.exec_uid,
        case_uid,
        "preferences",
        api.StepFormSubmit(answers={**answers, "fee_package_ids": [10**9]}),
        _principal(CASES_WRITE),
        _ctx(sess, fx),
    )

    assert refused["blocked"] is True
    assert "fee package" in str(refused["reason"])
    assert await _step_status(sess, fx, "preferences", None) == "blocked"
    # The artifact IS fulfilled — this is the state that had no way forward.
    assert (
        await _scalar(
            sess,
            "SELECT ri.status FROM documents.request_items ri "
            "JOIN flows.case_steps cs ON cs.request_item_id = ri.id "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'preferences'",
            e=fx.execution_id,
        )
        == "fulfilled"
    )

    # The form is still open, holding the answers that need putting right.
    reopened = await api.get_step_form(
        fx.exec_uid,
        case_uid,
        "preferences",
        principal=_principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )
    assert reopened.editable is True
    assert (reopened.answers or {})["account_name"] == "Growth SIPP"

    corrected = await api.submit_step_form(
        fx.exec_uid,
        case_uid,
        "preferences",
        api.StepFormSubmit(answers={**answers, "fee_package_ids": []}),
        _principal(CASES_WRITE),
        _ctx(sess, fx),
    )

    assert corrected["corrected"] is True
    assert corrected["amended"] is False  # the step never completed, so the graph must still move
    assert await _step_status(sess, fx, "preferences", None) == "fulfilled"


@pytest.mark.asyncio
async def test_step_form_needs_cases_write(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            fx.exec_uid,
            fx.case_uid,
            "core",
            api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact),
            principal=_principal(CASES_READ),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 403


# ==================================================================================================
# the same invariants through the OTHER door (`POST /{uid}/action`)
#
# Both guards above used to live only in ``submit_step_form``. ``/action`` reaches the same
# transition, with the same ``cases:write`` permission, and completed a fill_form step — including a
# client-assigned one — with no answers at all: ``FillFormHandler.handle`` returned COMPLETED
# whenever ``detail`` carried no answers dict. They now live at the transition
# (``orchestrator._assert_fulfilment_supported``), so every route inherits them; these are the
# bypass attempts, run against the guard.
# ==================================================================================================
@pytest.mark.asyncio
async def test_action_cannot_complete_a_client_assigned_form_step(
    service_session: AsyncSession,
) -> None:
    """The exact probe that defeated the route-local guard: skip the form endpoint entirely and
    'action' the client's own fact-find instead."""
    sess = service_session
    fx = await _seed(sess, assignee="client")
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(
                case_uid=fx.case_uid,
                step_key="core",
                outcome="passed",
                participant_contact_id=fx.contact,
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.form_responses WHERE tenant_id = :t",
                t=fx.tenant,
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_action_cannot_complete_a_staff_assigned_form_step_either(
    service_session: AsyncSession,
) -> None:
    """The variation an assignee-only guard would miss: the step IS staff-assigned, so 'only staff
    may do this' is satisfied — but there is still no form filled in."""
    sess = service_session
    fx = await _seed(sess, assignee="staff")
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(
                case_uid=fx.case_uid, step_key="core", participant_contact_id=fx.contact
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("step_key", ["terms", "proof"])
async def test_action_cannot_complete_an_acknowledgement_or_an_upload(
    service_session: AsyncSession, step_key: str
) -> None:
    """The other two artifact steps: ticking off 'accept the terms' or 'proof of address' from the
    staff side would record that the client agreed / supplied a document when they never did."""
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(
                case_uid=fx.case_uid, step_key=step_key, participant_contact_id=fx.contact
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert await _step_status(sess, fx, step_key, fx.contact) != "fulfilled"


@pytest.mark.asyncio
async def test_action_cannot_complete_a_form_step_that_has_no_form_item(
    service_session: AsyncSession,
) -> None:
    """The other shape of "no artifact": ``request_item_id IS NULL``. An application-scoped form has
    nobody to ask (it blocks with ``no_actor``), and a retried step is momentarily in the same state
    — so a guard that only checked the item's *status* would wave both through."""
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    assert (
        await _scalar(
            sess,
            "SELECT cs.request_item_id FROM flows.case_steps cs "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'orphan'",
            e=fx.execution_id,
        )
        is None
    )
    group_case = str(
        await _scalar(
            sess,
            "SELECT c.uid FROM flows.cases c JOIN flows.case_steps cs ON cs.case_id = c.id "
            "WHERE c.execution_id = :e AND cs.step_key = 'orphan'",
            e=fx.execution_id,
        )
    )
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(case_uid=group_case, step_key="orphan"),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert await _step_status(sess, fx, "orphan", None) != "fulfilled"


@pytest.mark.asyncio
async def test_action_cannot_re_complete_a_step_that_is_already_done(
    service_session: AsyncSession,
) -> None:
    """A settled step must not re-transition: replaying it would re-run the module's side effects
    (re-apply the CRM patch, overwrite a recorded outcome)."""
    sess = service_session
    fx = await _seed(sess)
    await api.submit_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert await _step_status(sess, fx, "core", fx.contact) == "fulfilled"
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(
                case_uid=fx.case_uid, step_key="core", participant_contact_id=fx.contact
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_action_still_completes_a_manual_step(service_session: AsyncSession) -> None:
    """The guard is evidence-based, not a blanket 'no completing from /action': a compliance
    review has no artifact to produce and must still be actionable, or the journey wedges."""
    sess = service_session
    fx = await _seed(
        sess,
        steps=[
            {
                "id": "offshore_review",
                "type": "review",
                "title": "Compliance review",
                "scope": "application",
                "assignee": "staff",
                "config": {},
            }
        ],
    )
    review_case_uid = str(
        await _scalar(
            sess,
            "SELECT c.uid FROM flows.cases c JOIN flows.case_steps cs ON cs.case_id = c.id "
            "WHERE c.execution_id = :e AND cs.step_key = 'offshore_review'",
            e=fx.execution_id,
        )
    )
    await api.action_step(
        fx.exec_uid,
        api.ActionBody(
            case_uid=review_case_uid,
            step_key="offshore_review",
            outcome="passed",
            note="Source of wealth evidenced.",
        ),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert await _step_status(sess, fx, "offshore_review", None) == "fulfilled"


@pytest.mark.asyncio
async def test_action_cannot_pass_or_refer_an_identity_check(service_session: AsyncSession) -> None:
    """Identity is a mandatory check settled by the check itself — the digital provider's result or
    the manual documents landing — never by a verdict typed at the step. Pass / Refer used to sit
    on the row; the journey page no longer offers them, and this is the API's half of the rule so
    the transition is not one form-post away for anyone holding ``cases:write``. Even with both
    documents on file: the evidence completing the step is the engine's job."""
    sess = service_session
    fx = await _seed(sess)
    recorded = await _scalar(
        sess,
        "SELECT cs.data -> 'identity_documents' FROM flows.case_steps cs "
        "JOIN flows.cases c ON c.id = cs.case_id "
        "WHERE c.uid = :u AND cs.step_key = 'idv' AND cs.participant_subject_id = :p",
        u=fx.case_uid,
        p=fx.contact,
    )
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    if isinstance(recorded, dict) and recorded:
        await sess.execute(
            text("UPDATE documents.request_items SET status = 'fulfilled' WHERE uid = ANY(:u)"),
            {"u": [str(uid) for uid in recorded.values()]},
        )
    for outcome in ("passed", "referred"):
        with pytest.raises(HTTPException) as exc:
            await api.action_step(
                fx.exec_uid,
                api.ActionBody(
                    case_uid=fx.case_uid,
                    step_key="idv",
                    outcome=outcome,
                    note="Passport checked.",
                    participant_contact_id=fx.contact,
                ),
                principal=_principal(CASES_WRITE),
                ctx=_ctx(sess, fx),
            )
        assert exc.value.status_code == 422
        assert "not passed or referred by hand" in str(exc.value.detail)
    assert await _step_status(sess, fx, "idv", fx.contact) != "fulfilled"


@pytest.mark.asyncio
async def test_a_form_short_of_its_required_answers_is_refused_at_the_transition(
    service_session: AsyncSession,
) -> None:
    """The required-fields rule now lives at the transition, so it also covers the CLIENT's own
    fulfilment endpoint (documents.py, which validates nothing): fulfil the item and store an empty
    response exactly as that endpoint does, then drive the hook it drives."""
    sess = service_session
    fx = await _seed(sess, assignee="client")
    item_id = int(
        await _scalar(
            sess,
            "SELECT cs.request_item_id FROM flows.case_steps cs "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'core'",
            e=fx.execution_id,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO documents.form_responses "
            "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers) "
            "VALUES (:u, :t, :i, :f, :c, CAST(:a AS jsonb))"
        ),
        {
            "u": f"frsp_{uuid.uuid4().hex}",
            "t": fx.tenant,
            "i": item_id,
            "f": fx.form_uid,
            "c": fx.contact,
            "a": json.dumps({"first_name": ""}),
        },
    )
    await sess.execute(
        text("UPDATE documents.request_items SET status = 'fulfilled' WHERE id = :i"),
        {"i": item_id},
    )
    with pytest.raises(HTTPException) as exc:
        await orchestrator.on_item_fulfilled(sess, item_id)
    assert exc.value.status_code == 422
    assert "First name" in str(exc.value.detail)
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"


@pytest.mark.asyncio
async def test_the_clients_own_completed_form_still_advances_the_step(
    service_session: AsyncSession,
) -> None:
    """The other half of the previous test: a client submission that DOES answer the form completes
    the step through the same hook (the guard must not break the path it protects)."""
    sess = service_session
    fx = await _seed(sess, assignee="client")
    item_id = int(
        await _scalar(
            sess,
            "SELECT cs.request_item_id FROM flows.case_steps cs "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'core'",
            e=fx.execution_id,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO documents.form_responses "
            "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers) "
            "VALUES (:u, :t, :i, :f, :c, CAST(:a AS jsonb))"
        ),
        {
            "u": f"frsp_{uuid.uuid4().hex}",
            "t": fx.tenant,
            "i": item_id,
            "f": fx.form_uid,
            "c": fx.contact,
            "a": json.dumps({"first_name": "Ada"}),
        },
    )
    await sess.execute(
        text("UPDATE documents.request_items SET status = 'fulfilled' WHERE id = :i"),
        {"i": item_id},
    )
    await orchestrator.on_item_fulfilled(sess, item_id)
    assert await _step_status(sess, fx, "core", fx.contact) == "fulfilled"


# ==================================================================================================
# the fanned-participant guards
# ==================================================================================================
@pytest.mark.asyncio
async def test_actioning_a_fanned_step_without_a_participant_is_422(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, participants=2)
    with pytest.raises(HTTPException) as exc:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(case_uid=fx.case_uid, step_key="idv", participant_contact_id=None),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    assert await _step_status(sess, fx, "idv", fx.contacts[0]) != "fulfilled"


@pytest.mark.asyncio
async def test_retrying_a_fanned_step_without_a_participant_is_422(
    service_session: AsyncSession,
) -> None:
    """``/retry`` had no guard: with the participant omitted the orchestrator reset EVERY instance
    of the step key (clearing their request items) and re-instantiated only one."""
    sess = service_session
    fx = await _seed(sess, participants=2)
    case_id = int(await _scalar(sess, "SELECT id FROM flows.cases WHERE uid = :u", u=fx.case_uid))
    await sess.execute(
        text(
            "UPDATE flows.case_steps SET status = 'failed' WHERE case_id = :c AND step_key = 'idv'"
        ),
        {"c": case_id},
    )
    with pytest.raises(HTTPException) as exc:
        await api.retry_step(
            fx.exec_uid,
            api.RetryBody(case_uid=fx.case_uid, step_key="idv", participant_contact_id=None),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422
    # Nothing was reset.
    assert await _step_status(sess, fx, "idv", fx.contacts[0]) == "failed"


@pytest.mark.asyncio
async def test_retry_only_resets_the_named_participants_instance(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, participants=2)
    other_case_uid = str(
        await _scalar(
            sess,
            "SELECT c.uid FROM flows.cases c WHERE c.execution_id = :e AND c.subject_id = :s",
            e=fx.execution_id,
            s=fx.contacts[1],
        )
    )
    case_id = int(
        await _scalar(sess, "SELECT id FROM flows.cases WHERE uid = :u", u=other_case_uid)
    )
    await sess.execute(
        text(
            "UPDATE flows.case_steps SET status = 'failed' WHERE case_id = :c AND step_key = 'idv'"
        ),
        {"c": case_id},
    )
    await api.retry_step(
        fx.exec_uid,
        api.RetryBody(
            case_uid=other_case_uid, step_key="idv", participant_contact_id=fx.contacts[1]
        ),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert await _step_status(sess, fx, "idv", fx.contacts[1]) == "waiting"


@pytest.mark.asyncio
async def test_retrying_a_step_that_is_not_failed_is_422(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.retry_step(
            fx.exec_uid,
            api.RetryBody(case_uid=fx.case_uid, step_key="core", participant_contact_id=fx.contact),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422


# ==================================================================================================
# cancel
# ==================================================================================================
@pytest.mark.asyncio
async def test_cancel_stops_the_journey_and_is_refused_twice(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    out = await api.cancel_application(
        fx.exec_uid,
        api.CancelBody(reason="started in error"),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )
    assert out == {"ok": True}
    assert (
        await _scalar(sess, "SELECT status FROM flows.executions WHERE id = :e", e=fx.execution_id)
        == "cancelled"
    )
    with pytest.raises(HTTPException) as exc:
        await api.cancel_application(
            fx.exec_uid,
            api.CancelBody(reason=None),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_cancel_needs_cases_write(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.cancel_application(
            fx.exec_uid,
            api.CancelBody(reason=None),
            principal=_principal(CASES_READ),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cancelling_an_unknown_journey_is_404(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    with pytest.raises(HTTPException) as exc:
        await api.cancel_application(
            "fex_nope",
            api.CancelBody(reason=None),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, fx),
        )
    assert exc.value.status_code == 404


# ==================================================================================================
# in-person acceptance of a client's terms
#
# The refusals above are the point: staff must not be able to *tick off* an acknowledgement. But
# plenty of onboarding happens with the client in the room, and there the adviser is the one at a
# keyboard. `/acknowledge` is that case — it files the acknowledgement for real and stamps who
# attested to it, so the trail can tell an in-person acceptance from one the client clicked.
#
# It is recorded against a SIGNED COPY, and only against one: the upload is required, filed on
# the client's record, and linked from the item, the step and the audit entry.
# ==================================================================================================
_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


def _signed_copy(name: str = "signed-terms.pdf") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(_PDF), size=len(_PDF))


def _fake_document_store(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Object storage is out of scope here: record the keys written instead of writing them."""
    written: list[str] = []

    async def _bucket(_session: AsyncSession, _tenant_id: int) -> str:
        return "documents-test"

    async def _put(
        key: str, _data: bytes, _content_type: str, *, bucket: str | None = None
    ) -> None:
        assert bucket == "documents-test"
        written.append(key)

    monkeypatch.setattr(tenant_storage, "document_bucket", _bucket)
    monkeypatch.setattr(storage, "put_document", _put)
    return written


async def _acknowledge(
    sess: AsyncSession,
    fx: _Fx,
    *,
    participant: int | None,
    attested: bool = True,
    note: str | None = None,
    file: UploadFile | None = None,
    principal: Principal | None = None,
    step_key: str = "terms",
) -> dict[str, object]:
    return await api.acknowledge_terms(
        fx.exec_uid,
        case_uid=fx.case_uid,
        step_key=step_key,
        participant_contact_id=participant,
        attested=attested,
        note=note,
        file=file or _signed_copy(),
        principal=principal or _principal(CASES_WRITE),
        ctx=_ctx(sess, fx),
    )


async def _terms_item(sess: AsyncSession, fx: _Fx) -> tuple[Any, str]:
    """Give the seeded `terms` step a real target document (the seed points at a missing uid, which
    is itself a case worth covering). Returns the document id (a uuid) + uid."""
    doc_uid = f"doc_{uuid.uuid4().hex[:8]}"
    doc_id = await _scalar(
        sess,
        "INSERT INTO documents.documents "
        "  (uid, tenant_id, document_type, source, name, status) "
        "VALUES (:u, :t, 'terms', 'generated', 'General declaration', 'generated') "
        "RETURNING id",
        u=doc_uid,
        t=fx.tenant,
    )
    await sess.execute(
        text(
            "UPDATE documents.request_items SET target_document_id = :d "
            "WHERE id = (SELECT cs.request_item_id FROM flows.case_steps cs "
            "  JOIN flows.cases c ON c.id = cs.case_id "
            "  WHERE c.execution_id = :e AND cs.step_key = 'terms')"
        ),
        {"d": doc_id, "e": fx.execution_id},
    )
    return doc_id, doc_uid


@pytest.mark.asyncio
async def test_acknowledge_files_the_acceptance_and_completes_the_step(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    written = _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    doc_id, doc_uid = await _terms_item(sess, fx)
    out = await _acknowledge(
        sess, fx, participant=fx.contact, note="Read aloud in the 14 July meeting"
    )
    assert await _step_status(sess, fx, "terms", fx.contact) == "fulfilled"
    # The artifact exists — this is a real fulfilment, not a status flip — and it points at the
    # signed copy the way an upload item points at its file.
    item = (
        (
            await sess.execute(
                text(
                    "SELECT ri.status, sd.uid AS signed_uid, sd.document_type, sd.name "
                    "FROM documents.request_items ri "
                    "JOIN flows.case_steps cs ON cs.request_item_id = ri.id "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "LEFT JOIN documents.documents sd ON sd.id = ri.fulfilled_document_id "
                    "WHERE c.execution_id = :e AND cs.step_key = 'terms'"
                ),
                {"e": fx.execution_id},
            )
        )
        .mappings()
        .one()
    )
    assert item["status"] == "fulfilled"
    assert item["signed_uid"] == out["document_uid"]
    assert item["document_type"] == "signed_terms"
    assert item["name"] == "signed-terms.pdf"
    assert len(written) == 1  # the signed copy reached the store
    # The signed copy is the CLIENT's document, on their record.
    assert await _scalar(
        sess,
        "SELECT 1 FROM documents.document_contacts dc JOIN documents.documents d "
        "  ON d.id = dc.document_id WHERE d.uid = :u AND dc.contact_id = :c AND dc.role = 'owner'",
        u=out["document_uid"],
        c=fx.contact,
    )
    # The step renders `data.document_id`, so the journey row shows the signed copy.
    assert (
        await _scalar(
            sess,
            "SELECT d.uid FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "JOIN documents.documents d ON d.id = (cs.data ->> 'document_id')::uuid "
            "WHERE c.execution_id = :e AND cs.step_key = 'terms'",
            e=fx.execution_id,
        )
        == out["document_uid"]
    )
    # The client can still see what they accepted.
    assert await _scalar(
        sess,
        "SELECT 1 FROM documents.document_contacts "
        "WHERE document_id = :d AND contact_id = :c AND role = 'recipient'",
        d=doc_id,
        c=fx.contact,
    )
    # And the trail says it was taken in person, and against what — the reason this endpoint is
    # allowed to exist.
    detail = await _scalar(
        sess,
        "SELECT al.detail FROM documents.audit_log al JOIN documents.documents d ON d.id = "
        "  al.document_id WHERE d.uid = :u AND al.action = 'document.acknowledged'",
        u=doc_uid,
    )
    parsed = detail if isinstance(detail, dict) else json.loads(detail)
    assert parsed["in_person"] is True
    assert parsed["note"] == "Read aloud in the 14 July meeting"
    assert parsed["signed_copy"] == out["document_uid"]


@pytest.mark.asyncio
async def test_acknowledge_without_a_signed_copy_is_refused(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload is what makes this an acceptance rather than an assertion. A file the store
    refuses (wrong type here; over the cap is the same path) leaves the acceptance unrecorded —
    the copy is validated before anything else is written."""
    sess = service_session
    written = _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    await _terms_item(sess, fx)
    with pytest.raises(HTTPException) as exc:
        await _acknowledge(
            sess,
            fx,
            participant=fx.contact,
            file=UploadFile(filename="notes.txt", file=BytesIO(b"they said yes"), size=13),
        )
    assert exc.value.status_code == 422
    assert written == []
    assert await _step_status(sess, fx, "terms", fx.contact) != "fulfilled"
    assert (
        await _scalar(
            sess,
            "SELECT ri.status FROM documents.request_items ri "
            "JOIN flows.case_steps cs ON cs.request_item_id = ri.id "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'terms'",
            e=fx.execution_id,
        )
        == "pending"
    )


@pytest.mark.asyncio
async def test_acknowledge_without_the_attestation_is_refused(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``attested`` is explicit on purpose: an accidental or replayed POST must not record a
    client's consent. Nothing is written when it is missing — not even the file."""
    sess = service_session
    written = _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    with pytest.raises(HTTPException) as exc:
        await _acknowledge(sess, fx, participant=fx.contact, attested=False)
    assert exc.value.status_code == 422
    assert written == []
    assert await _step_status(sess, fx, "terms", fx.contact) != "fulfilled"


@pytest.mark.asyncio
async def test_acknowledge_refuses_a_step_that_is_not_a_terms_acknowledgement(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must not become a general-purpose completion door. A fact-find has answers as its
    evidence; a document upload has a file. Neither is an acknowledgement."""
    sess = service_session
    _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    for key in ("core", "proof", "idv"):
        with pytest.raises(HTTPException) as exc:
            await _acknowledge(sess, fx, participant=fx.contact, step_key=key)
        assert exc.value.status_code == 422
        assert await _step_status(sess, fx, key, fx.contact) != "fulfilled"


@pytest.mark.asyncio
async def test_acknowledging_the_same_terms_twice_is_refused(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    await _terms_item(sess, fx)
    await _acknowledge(sess, fx, participant=fx.contact)
    with pytest.raises(HTTPException) as exc:
        await _acknowledge(sess, fx, participant=fx.contact)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_acknowledging_a_fanned_step_without_a_participant_is_422(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two owners, one step key. Without the participant the write would record BOTH owners as
    having accepted on one click — the same trap ``/action`` and ``/retry`` carry a guard for."""
    sess = service_session
    _fake_document_store(monkeypatch)
    fx = await _seed(sess, participants=2, artifacts=True)
    with pytest.raises(HTTPException) as exc:
        await _acknowledge(sess, fx, participant=None)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_acknowledge_needs_cases_write(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    fx = await _seed(sess, artifacts=True)
    with pytest.raises(HTTPException) as exc:
        await _acknowledge(sess, fx, participant=fx.contact, principal=_principal(CASES_READ))
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------------------------------
# GET /{exec_uid}/terms -- the printable copy. The acceptance above is recorded against a signed
# copy, and the client can only sign what somebody handed them: this is the declaration behind the
# step, read off the SAME snapshot the client is shown, for the adviser to print.
# --------------------------------------------------------------------------------------------------
async def _terms_copy(
    sess: AsyncSession,
    fx: _Fx,
    *,
    participant: int | None = None,
    step_key: str = "terms",
    principal: Principal | None = None,
) -> api.TermsCopyOut:
    return await api.terms_copy(
        fx.exec_uid,
        case_uid=fx.case_uid,
        step_key=step_key,
        participant_contact_id=participant,
        principal=principal or _principal(CASES_READ),
        ctx=_ctx(sess, fx),
    )


@pytest.mark.asyncio
async def test_terms_copy_returns_the_inline_declaration_sanitised(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    doc_id, _ = await _terms_item(sess, fx)
    await sess.execute(
        text(
            "UPDATE documents.documents SET metadata = CAST(:m AS jsonb) WHERE id = :d",
        ),
        {
            "m": json.dumps(
                {"content": "<h2>Declaration</h2><p>I agree.</p><script>alert(1)</script>"}
            ),
            "d": doc_id,
        },
    )
    out = await _terms_copy(sess, fx, participant=fx.contact)
    assert out.name == "General declaration"
    assert out.content is not None
    assert "<h2>Declaration</h2>" in out.content
    assert "<script" not in out.content
    # Inline copy ⇒ nothing to stream; the print page renders the HTML.
    assert out.document_uid is None
    assert out.content_type is None
    # The participant is a filter, not a requirement: one target serves every instance.
    same = await _terms_copy(sess, fx, participant=None)
    assert same.content == out.content


@pytest.mark.asyncio
async def test_terms_copy_names_the_stored_file_when_the_terms_are_a_pdf(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    doc_id, doc_uid = await _terms_item(sess, fx)
    file_id = await _scalar(
        sess,
        "INSERT INTO documents.files "
        "  (tenant_id, storage_key, content_type, size_bytes, sha256, bucket) "
        "VALUES (:t, :k, 'application/pdf', 1024, :sha, 'documents-test') RETURNING id",
        t=fx.tenant,
        k=f"terms/{doc_uid}.pdf",
        sha=uuid.uuid4().hex * 2,
    )
    await sess.execute(
        text("UPDATE documents.documents SET original_file_id = :f WHERE id = :d"),
        {"f": file_id, "d": doc_id},
    )
    out = await _terms_copy(sess, fx, participant=fx.contact)
    assert out.content is None
    assert out.document_uid == doc_uid
    assert out.content_type == "application/pdf"


@pytest.mark.asyncio
async def test_terms_copy_refuses_a_step_that_is_not_terms_and_one_with_no_declaration(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    with pytest.raises(HTTPException) as exc:
        await _terms_copy(sess, fx, participant=fx.contact, step_key="core")
    assert exc.value.status_code == 422
    # The seed points the terms step at a missing document uid: an item with no target.
    with pytest.raises(HTTPException) as exc:
        await _terms_copy(sess, fx, participant=fx.contact)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await _terms_copy(sess, fx, participant=fx.contact, step_key="nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_terms_copy_needs_cases_read(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess, artifacts=True)
    await _terms_item(sess, fx)
    with pytest.raises(HTTPException) as exc:
        await _terms_copy(sess, fx, participant=fx.contact, principal=_principal())
    assert exc.value.status_code == 403


# ==================================================================================================
# /applications/me -- the whole-journey milestone rollup
# ==================================================================================================
def _row(step_key: str, status_: str = "pending", assignee: str = "staff") -> dict[str, Any]:
    return {"step_key": step_key, "status": status_, "assignee": assignee}


def test_stage_rollup_is_empty_with_no_running_case() -> None:
    """No steps at all (an unlinked principal, or a client with no application in flight) must be
    ``[]``, not a full stage list sitting at 'upcoming' -- the block hides on an empty list."""
    assert api._stage_summary([]) == []


def test_a_step_absent_from_every_bucket_is_never_counted() -> None:
    """A gate/condition id (or a step type this map predates) contributes to no bucket, so a run
    that only ever advances through gates still resolves every stage as done -- never blocking on
    a step it will never see in ANY bucket."""
    stages = api._stage_summary(
        [_row("is_individual", "fulfilled"), _row("has_pension", "fulfilled")]
    )
    assert {s["key"]: s["status"] for s in stages} == {
        "details": "done",
        "verification": "done",
        "sign": "done",
        "setup": "done",
        "complete": "done",
    }


def test_a_skipped_conditional_step_never_instantiates_so_its_stage_still_clears() -> None:
    """No SIPP => no "employer" row at all for this applicant; "details" must still read done once
    the rows that DID instantiate (core, additional_info) are fulfilled -- a stage with nothing
    left to wait on is not a stage still in progress."""
    stages = api._stage_summary(
        [_row("core", "fulfilled"), _row("additional_info", "fulfilled"), _row("idv", "pending")]
    )
    by_key = {s["key"]: s["status"] for s in stages}
    assert by_key["details"] == "done"
    # Terms (sign) come before identity/AML on the spine — with only idv outstanding,
    # sign has no rows so it clears, and verification is current.
    assert by_key["sign"] == "done"
    assert by_key["verification"] == "current"
    assert by_key["setup"] == "upcoming"
    assert by_key["complete"] == "upcoming"


def test_a_journey_running_on_the_old_step_key_keeps_its_milestone() -> None:
    """`offshore_kyc` was renamed `additional_info`; a journey already in flight keeps the old one.

    The seed publishes a NEW flow version and repoints the flow, but an execution pins its own
    version — so its `flows.case_steps` rows still say `offshore_kyc` long after the rename. This
    map classifies by KEY and an unknown key counts toward no stage at all, so without the former
    spelling in the `details` bucket the step would silently drop out of the milestone strip for
    every client mid-application: "Getting your details" would read done while a form was still
    outstanding. Delete the old key here only once no such execution is live.
    """
    outstanding = api._stage_summary([_row("core", "fulfilled"), _row("offshore_kyc", "pending")])
    assert {s["key"]: s["status"] for s in outstanding}["details"] == "current"
    settled = api._stage_summary(
        [_row("core", "fulfilled"), _row("offshore_kyc", "fulfilled"), _row("idv", "pending")]
    )
    assert {s["key"]: s["status"] for s in settled}["details"] == "done"


def test_the_first_stage_with_an_outstanding_step_is_current_and_nothing_after_it_jumps_ahead() -> (
    None
):
    """The spine is sequential — terms before identity. An earlier outstanding stage stays
    current even if a later step row is incorrectly fulfilled."""
    stages = api._stage_summary(
        [
            _row("core", "fulfilled"),
            _row("terms_general", "pending", "client"),
            _row("idv", "fulfilled"),  # out of order; must not mislead
            _row("aml", "pending"),
        ]
    )
    by_key = {s["key"]: s["status"] for s in stages}
    assert by_key["sign"] == "current"
    assert by_key["verification"] == "upcoming"


def test_two_journeys_of_the_same_flow_do_not_share_stage_keys() -> None:
    """A repeated stage key blanks the client portal. Two onboardings are the same flow, so the
    prefix has to be the execution, not the flow code — `onboarding:details` twice is still one
    key, and the portal's `{#each}` throws and clears the page."""

    def row(execution_id: int, uid: str, step_key: str, status_: str) -> dict[str, Any]:
        return {
            "execution_id": execution_id,
            "execution_uid": uid,
            "flow_code": "onboarding",
            "step_key": step_key,
            "status": status_,
        }

    stages = api._client_stages(
        [
            row(1, "donejourney1", "core", "fulfilled"),
            row(2, "livejourney2", "core", "pending"),
        ]
    )
    keys = [stage["key"] for stage in stages]
    assert len(keys) == len(set(keys))
    assert "donejourney1:details" in keys
    assert "livejourney2:details" in keys


def test_a_fully_fulfilled_journey_is_complete() -> None:
    all_keys = [k for _, _, keys in api._ONBOARDING_STAGES for k in keys]
    stages = api._stage_summary([_row(k, "fulfilled") for k in all_keys])
    assert [s["status"] for s in stages] == ["done"] * len(stages)
    assert stages[-1] == {"key": "complete", "label": "Complete", "status": "done"}


@pytest.mark.asyncio
async def test_my_onboarding_reports_stages_alongside_the_existing_client_step_list(
    service_session: AsyncSession,
) -> None:
    """End-to-end wiring: a real running case's staff-assigned steps ('core', 'idv' -- the default
    two-step definition) drive `stages`, without leaking into the unchanged `steps`/`done`/`total`
    trio, which stay client-only exactly as before."""
    sess = service_session
    fx = await _seed(sess, assignee="staff", flow_code="onboarding")
    # `my_onboarding` scopes by auth.contact_scope(), which reads the request.jwt.claims a real
    # request has via firm_session's own auth.resolve_claims() call -- calling the coroutine
    # directly (as every other test in this file does) skips that, so the claim is set by hand,
    # exactly as db.py's own tenant_session/firm_session would have published it.
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    out = await api.my_onboarding(
        principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
        ctx=FirmContext(
            session=sess,
            tenant_id=fx.tenant,
            principal=_principal(),
        ),
    )
    # Both seeded steps are staff-assigned, so the client's own list is empty...
    assert out["steps"] == []
    assert out["done"] == 0
    assert out["total"] == 0
    # ...but the milestone rollup still reflects a case in flight: "details" (core) is the current
    # stage since nothing is fulfilled yet; sign and verification sit behind it.
    by_key = {s["key"]: s["status"] for s in out["stages"]}
    assert by_key["details"] == "current"
    assert by_key["sign"] == "upcoming"
    assert by_key["verification"] == "upcoming"


# ==================================================================================================
# GET /applications/{uid} -- the profile cards' portal-access status
# ==================================================================================================
def _contact_case(out: dict[str, Any], contact_id: int) -> dict[str, Any]:
    return next(
        c for c in out["cases"] if c["subject_type"] == "contact" and c["subject_id"] == contact_id
    )


@pytest.mark.asyncio
async def test_a_fresh_contact_has_no_portal_access(service_session: AsyncSession) -> None:
    """No `crm.contact_users` row at all -- the ordinary state for a client an application just
    created inline -- reports 'none', not a missing/null key the frontend has to special-case."""
    sess = service_session
    fx = await _seed(sess)
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    case = _contact_case(out, fx.contact)
    assert case["portal_status"] == "none"
    assert case["portal_login_email"] is None
    assert case["contact_email"] and case["contact_email"].endswith("@t.test")


@pytest.mark.asyncio
async def test_the_applicant_card_carries_date_of_birth_and_phone(
    service_session: AsyncSession,
) -> None:
    """The card names who the applicant IS: their date of birth (which is also what marks a JISA
    holder as a child) and the number staff ring. `contact_type` never says `child`, so without
    the date the portal had no way to tell a child from an adult."""
    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text(
            "UPDATE crm.contacts SET dob = DATE '2014-03-03', mobile = '+447700900123' "
            "WHERE id = :c"
        ),
        {"c": fx.contact},
    )
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    case = _contact_case(out, fx.contact)
    assert str(case["dob"]) == "2014-03-03"
    assert case["contact_phone"] == "+447700900123"
    account_case = next(c for c in out["cases"] if c["subject_type"] == "account")
    assert account_case["dob"] is None
    assert account_case["contact_phone"] is None


@pytest.mark.asyncio
async def test_the_activity_names_who_completed_the_step(service_session: AsyncSession) -> None:
    """The event stores the actor as a user id. The journey log needs the person's name, or it
    can only show whose form the step was, which reads as if that client did the work."""
    sess = service_session
    fx = await _seed(sess)
    s = uuid.uuid4().hex[:8]
    actor_id = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
            "  last_name, enabled) "
            "VALUES (:uid, :t, :e, :w, 'Gio', 'Matonti', true) RETURNING id",
            uid=f"usr_{s}",
            t=fx.tenant,
            e=f"gio_{s}@t.test",
            w=f"wos_{s}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO flows.domain_events "
            "  (tenant_id, execution_id, event_type, payload, idempotency_key) "
            "VALUES (:t, :e, 'step.completed', CAST(:p AS jsonb), :k)"
        ),
        {
            "t": fx.tenant,
            "e": fx.execution_id,
            "p": json.dumps({"step_key": "core", "actor": actor_id}),
            "k": f"actor-{s}",
        },
    )
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    completed = next(
        event for event in out["timeline"] if event["payload"].get("step_key") == "core"
    )
    assert completed["payload"]["actor_name"] == "Gio Matonti"


async def _link_login(
    sess: AsyncSession,
    *,
    tenant: int,
    contact_id: int,
    membership_status: str,
    enabled: bool = True,
) -> None:
    s = uuid.uuid4().hex[:8]
    user_id = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
            "  last_name, enabled) "
            "VALUES (:uid, :t, :e, :w, 'Test', 'Client', :en) RETURNING id",
            uid=f"usr_{s}",
            t=tenant,
            e=f"login_{s}@t.test",
            w=f"wos_{s}",
            en=enabled,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
            "VALUES (:t, :c, :u, 'self')"
        ),
        {"t": tenant, "c": contact_id, "u": user_id},
    )
    await sess.execute(
        text(
            "INSERT INTO auth.organisation_memberships "
            "  (user_id, tenant_id, role_id, portal, status) "
            "VALUES (:u, :t, NULL, 'client', :st)"
        ),
        {"u": user_id, "t": tenant, "st": membership_status},
    )


@pytest.mark.asyncio
async def test_an_invited_login_reports_invited(service_session: AsyncSession) -> None:
    """`enabled=False` is the REAL state of a freshly-granted login (grant_portal_access sets
    `enabled = email_verified`, which is false until the client sets a password) — this pins the
    exact bug found by hand: checking `enabled` before `membership_status` reported every
    not-yet-opened invite as 'disabled' rather than 'invited'."""
    sess = service_session
    fx = await _seed(sess)
    await _link_login(
        sess, tenant=fx.tenant, contact_id=fx.contact, membership_status="pending", enabled=False
    )
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    case = _contact_case(out, fx.contact)
    assert case["portal_status"] == "invited"
    assert case["portal_login_email"]


@pytest.mark.asyncio
async def test_an_active_login_reports_active_and_wins_over_a_stale_second_login(
    service_session: AsyncSession,
) -> None:
    """A contact with two logins (rare, but the join can produce it) reports the MOST advanced
    state rather than whichever row the database happens to return first."""
    sess = service_session
    fx = await _seed(sess)
    await _link_login(sess, tenant=fx.tenant, contact_id=fx.contact, membership_status="inactive")
    await _link_login(sess, tenant=fx.tenant, contact_id=fx.contact, membership_status="active")
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    case = _contact_case(out, fx.contact)
    assert case["portal_status"] == "active"


@pytest.mark.asyncio
async def test_a_disabled_login_reports_disabled_even_if_its_membership_is_active(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _link_login(
        sess, tenant=fx.tenant, contact_id=fx.contact, membership_status="active", enabled=False
    )
    out = await api.get_application(
        exec_uid=fx.exec_uid, principal=_principal(CASES_READ), ctx=_ctx(sess, fx)
    )
    case = _contact_case(out, fx.contact)
    assert case["portal_status"] == "disabled"


# ==================================================================================================
# /applications/me -- the client review dossier (pre-terms)
# ==================================================================================================
_COMPLIANCE_LEAK_KEYS = frozenset(
    {
        "aml",
        "aml_status",
        "identity",
        "identity_status",
        "kyc",
        "kyc_status",
        "screening",
        "verified",
        "is_verified",
        "digital_verification",
        "pep",
        "sanctions",
    }
)


@pytest.mark.asyncio
async def test_my_onboarding_review_covers_you_without_leaking_coapplicant_compliance(
    service_session: AsyncSession,
) -> None:
    """Parity with the legacy overview/applicant/accounts review — WITHOUT the legacy defect that
    showed every co-applicant's AML and identity ticks. ``you`` is the signed-in client's own row;
    other applicants are name + type + role only."""
    sess = service_session
    fx = await _seed(sess, participants=2)
    await sess.execute(
        text(
            "UPDATE crm.contacts SET "
            "  first_name = 'Alice', surname = 'Able', mobile = '07111111111', "
            "  dob = DATE '1990-01-15', nationality = ARRAY['GB']::text[], "
            "  tax_residency = ARRAY['GB']::text[], "
            "  national_identifier = 'QQ123456C', national_identifier_type = 'nino', "
            "  address = CAST(:addr AS jsonb), bank_account = CAST(:bank AS jsonb) "
            "WHERE id = :c"
        ),
        {
            "c": fx.contact,
            "addr": json.dumps(
                {
                    "line1": "1 High Street",
                    "city": "London",
                    "postcode": "SW1A 1AA",
                    "country": "GB",
                }
            ),
            "bank": json.dumps(
                {"name": "Alice Able", "sort_code": "12-34-56", "account_number": "12345678"}
            ),
        },
    )
    await sess.execute(
        text(
            "UPDATE crm.contacts SET first_name = 'Bob', surname = 'Baker', "
            "  email = 'bob.secret@t.test' WHERE id = :c"
        ),
        {"c": fx.contacts[1]},
    )
    # Kickoff richness on the account: model, fee package, transfer summary, JISA child,
    # pension beneficiaries — what the client dossier must surface without leaking serial ids.
    model_id = int(
        await _scalar(
            sess,
            "INSERT INTO platform.models (provider, tenant_id, name) "
            "VALUES ("
            "  (SELECT provider FROM crm.applications WHERE id = "
            "    (SELECT crm_application_id FROM flows.executions WHERE id = :e)), "
            "  :t, 'Balanced Growth') RETURNING id",
            e=fx.execution_id,
            t=fx.tenant,
        )
    )
    fee_id = int(
        await _scalar(
            sess,
            "INSERT INTO platform.fee_packages "
            "  (tenant_id, provider, name, category, frequency, fee_type, rule) "
            "VALUES ("
            "  :t, "
            "  (SELECT provider FROM crm.applications WHERE id = "
            "    (SELECT crm_application_id FROM flows.executions WHERE id = :e)), "
            "  'Standard ongoing', 'platform', 'ongoing', 'percentage', "
            "  CAST(:rule AS jsonb)) RETURNING id",
            t=fx.tenant,
            e=fx.execution_id,
            rule=json.dumps({"rate": 0.0035}),
        )
    )
    await sess.execute(
        text(
            "UPDATE platform.accounts SET name = 'Joint SIPP', currency = 'GBP', "
            "  model_id = :m, fee_package_id = :f, "
            "  transfer = CAST(:xfer AS jsonb), "
            "  data = CAST(:data AS jsonb) "
            "WHERE application_id = ("
            "  SELECT crm_application_id FROM flows.executions WHERE id = :e"
            ")"
        ),
        {
            "e": fx.execution_id,
            "m": model_id,
            "f": fee_id,
            "xfer": json.dumps(
                {
                    "intent": True,
                    "transfer_type": "cash",
                    "amount": 12500.5,
                    "provider_id": 999,
                    "provider_name": "Ceding Co",
                }
            ),
            "data": json.dumps(
                {
                    "jisa_child": {"name": "Sam Junior", "dob": "2018-06-15"},
                    "beneficiaries": [
                        {"first_name": "Pat", "surname": "Able", "portion": 60},
                        {"first_name": "Kim", "surname": "Able", "portion": 40},
                    ],
                }
            ),
        },
    )
    account_id = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.accounts WHERE application_id = ("
            "  SELECT crm_application_id FROM flows.executions WHERE id = :e)",
            e=fx.execution_id,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO platform.account_fee_packages "
            "  (tenant_id, account_id, fee_package_id, sort_order) "
            "VALUES (:t, :a, :f, 0)"
        ),
        {"t": fx.tenant, "a": account_id, "f": fee_id},
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    out = await api.my_onboarding(
        principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
    )
    review = out["review"]
    assert isinstance(review, dict)
    assert review["application_uid"]
    assert review["firm_name"]
    # `you` carries the signed-in client's verifiable detail — and NOTHING compliance-shaped.
    you = review["you"]
    assert isinstance(you, dict)
    assert you["name"] == "Alice Able"
    assert you["email"] and str(you["email"]).endswith("@t.test")
    assert you["dob"] == "1990-01-15"
    assert you["nationality"] == ["GB"]
    assert you["tax_residency"] == ["GB"]
    assert you["national_identifier"] == "QQ123456C"
    assert you["mobile"] == "07111111111"
    assert you["address"] == {
        "line1": "1 High Street",
        "city": "London",
        "postcode": "SW1A 1AA",
        "country": "GB",
    }
    assert you["bank_account"] == {
        "name": "Alice Able",
        "sort_code": "12-34-56",
        "account_number": "12345678",
    }
    leak = _COMPLIANCE_LEAK_KEYS & set(you)
    assert not leak, f"review.you must not carry compliance fields; leaked {leak}"

    applicants = review["applicants"]
    assert isinstance(applicants, list) and len(applicants) == 2
    me = next(a for a in applicants if a["is_you"])
    other = next(a for a in applicants if not a["is_you"])
    assert isinstance(me["uid"], str) and me["uid"]
    assert isinstance(other["uid"], str) and other["uid"]
    assert "id" not in me and "id" not in other
    assert me["name"] == "Alice Able"
    assert me["role"] == "owner"
    assert me.get("email")
    assert other["name"] == "Bob Baker"
    assert other["type"] == "individual"
    assert other["role"] == "owner"
    assert "email" not in other, "co-applicant email is withheld"
    other_keys = set(other)
    leak = _COMPLIANCE_LEAK_KEYS & other_keys
    assert not leak, f"co-applicant must not carry compliance fields; leaked {leak}"
    # Only the public uid and safe display keys (email is for is_you alone).
    assert other_keys <= {"uid", "name", "type", "role", "is_you"}

    accounts = review["accounts"]
    assert isinstance(accounts, list) and len(accounts) == 1
    assert isinstance(accounts[0]["uid"], str) and accounts[0]["uid"]
    assert "id" not in accounts[0]
    assert accounts[0]["name"] == "Joint SIPP"
    assert accounts[0]["currency"] == "GBP"
    assert accounts[0]["has_transfer"] is True
    assert "Alice Able" in accounts[0]["owners"]
    assert "Bob Baker" in accounts[0]["owners"]
    assert accounts[0]["model"] == "Balanced Growth"
    assert accounts[0]["fee_packages"] == ["Standard ongoing"]
    # Both parties are owners here, so `owners` and `parties` agree — the JISA case (one owner,
    # one registered contact) is pinned separately. Roles travel so the portal can say who is who
    # instead of calling everyone on the account an owner.
    assert {(p["name"], p["role"]) for p in accounts[0]["parties"]} == {
        ("Alice Able", "owner"),
        ("Bob Baker", "owner"),
    }
    # The charge in full, not the firm's pricing label: a client agreeing to the application has
    # to be able to read the figure and how often it is charged.
    assert accounts[0]["fees"] == [
        {
            "name": "Standard ongoing",
            "fee_type": "percentage",
            "frequency": "ongoing",
            "currency": "GBP",
            "rule": {"rate": 0.0035},
        }
    ]
    assert accounts[0]["transfer"] == {
        "type": "cash",
        "amount": 12500.5,
        "provider_name": "Ceding Co",
    }
    assert "provider_id" not in accounts[0]["transfer"]
    assert accounts[0]["jisa_child"] == {"name": "Sam Junior", "dob": "2018-06-15"}
    assert accounts[0]["beneficiaries"] == [
        {"first_name": "Pat", "surname": "Able", "portion": 60.0},
        {"first_name": "Kim", "surname": "Able", "portion": 40.0},
    ]


async def _review_with_execution_status(sess: AsyncSession, status_value: str) -> object:
    """The dossier as the client portal reads it, with the journey put into one terminal state."""
    fx = await _seed(sess)
    await sess.execute(
        text("UPDATE flows.executions SET status = :s, completed_at = now() WHERE id = :e"),
        {"s": status_value, "e": fx.execution_id},
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    out = await api.my_onboarding(
        principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
    )
    return out["review"]


@pytest.mark.asyncio
async def test_my_onboarding_review_survives_the_journey_completing(
    service_session: AsyncSession,
) -> None:
    """THE REGRESSION: finishing onboarding made the client's own details disappear.

    This filter used to exclude `completed` as well as `cancelled` while the step query beside it
    excluded only `cancelled` — so the portal went on rendering the journey, every step ticked,
    beside a "Your details" card reading "Your adviser has not recorded personal details yet".
    The details were there; the dossier had stopped being willing to read them, and the empty
    state blamed the adviser.

    Nothing here goes stale on completion: `you` is selected from `crm.contacts`, so it is the
    client's CURRENT record rather than a snapshot of what was typed during the journey.
    """
    review = await _review_with_execution_status(service_session, "completed")
    assert isinstance(review, dict), "a completed journey still has a dossier"
    assert review["you"]["name"]
    assert review["you"]["email"]


@pytest.mark.asyncio
async def test_my_onboarding_review_is_null_for_a_cancelled_journey(
    service_session: AsyncSession,
) -> None:
    """Cancelled is the one terminal state that DOES withhold it: an abandoned application is not
    a record of anything, and the card's own words ("the personal details your adviser has
    entered") would be describing work that was called off."""
    assert await _review_with_execution_status(service_session, "cancelled") is None


@pytest.mark.asyncio
async def test_my_onboarding_review_reads_a_provider_phone_object_as_a_number(
    service_session: AsyncSession,
) -> None:
    """`crm.contacts.mobile` is a TEXT column and Seccl publishes its numbers as OBJECTS, so
    a book merged before the conformed model started extracting `->> 'number'` holds the whole
    thing where a number belongs. This card asks a client to confirm their own details, and it
    showed them `{"number": "07715215412", "isMobile": true, "locale": "en-GB"}` as their number.

    The OTHER numbers get the same rule. 0164 unwrapped what it backfilled and every write since
    goes through `normalise_phone_entry`, but the card is the last thing a client sees before they
    say "yes, that is me" — so it unwraps rather than trusting that nothing will ever write an
    object into an entry. Same rule the platform's own projections run (`app.contacts`), so the
    two portals cannot disagree about one client's number."""
    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )

    async def you_of() -> dict[str, object]:
        out = await api.my_onboarding(
            principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
            ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
        )
        review = out["review"]
        assert isinstance(review, dict)
        you = review["you"]
        assert isinstance(you, dict)
        return you

    await sess.execute(
        text("UPDATE crm.contacts SET mobile = :p WHERE id = :c"),
        {"c": fx.contact, "p": '{"number": "07715215412", "isMobile": true, "locale": "en-GB"}'},
    )
    assert (await you_of())["mobile"] == "07715215412"

    # A NUMBER is returned untouched — the trap in the obvious `::jsonb` implementation, which
    # raises on every ordinary value in the column (see tests/test_contact_phone.py).
    await sess.execute(
        text("UPDATE crm.contacts SET mobile = '07111111111' WHERE id = :c"),
        {"c": fx.contact},
    )
    assert (await you_of())["mobile"] == "07111111111"

    # The other number unwraps a stored Seccl object the same way the mobile does.
    await sess.execute(
        text("UPDATE crm.contacts SET phone = :n WHERE id = :c"),
        {"c": fx.contact, "n": '{"number": "01179000000", "isMobile": false}'},
    )
    assert (await you_of())["phone"] == "01179000000"

    await sess.execute(
        text("UPDATE crm.contacts SET phone = NULL WHERE id = :c"),
        {"c": fx.contact},
    )
    assert (await you_of())["phone"] is None


@pytest.mark.asyncio
async def test_application_pack_reads_the_mobile_column(
    service_session: AsyncSession,
) -> None:
    """0240 renamed ``crm.contacts.mobile_phone`` to ``mobile``. The pack PDF still selected the
    old name, so ``GET /applications/{uid}/application.pdf`` 500'd once a firm had migrated."""
    from app.flows.application_pack import load_application_pack

    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text("UPDATE crm.contacts SET mobile = '07700900123' WHERE id = :c"),
        {"c": fx.contact},
    )
    pack = await load_application_pack(sess, fx.tenant, fx.exec_uid)
    assert pack is not None
    phones = [
        row.value for person in pack.people for row in person.record if row.label == "Phone number"
    ]
    assert phones == ["07700900123"]


@pytest.mark.asyncio
async def test_my_onboarding_answers_for_the_active_contact_not_the_whole_login(
    service_session: AsyncSession,
) -> None:
    """A login linked to TWO clients (a guardian, a delegate, a couple sharing a login) has one
    of them active at a time, and `auth.contact_scope()` cannot tell which.

    Unscoped, this endpoint answered for both at once — and the client shell keys its whole
    navigation off the answer: while a journey runs the bar is "Onboarding" alone and `/`
    redirects there. So one contact still onboarding pinned EVERY client behind that login to
    that one's application, and the switcher's own destination is `/`, which sent them straight
    back. `review.you` was wrong in the same way: `is_you` is a scope test, so with both contacts
    named on one application it showed whichever the party ordering reached first."""
    sess = service_session
    fx = await _seed(sess, assignee="client", participants=2)
    for cid, first, last in ((fx.contacts[0], "Alice", "Able"), (fx.contacts[1], "Bob", "Baker")):
        await sess.execute(
            text("UPDATE crm.contacts SET first_name = :f, surname = :s WHERE id = :c"),
            {"c": cid, "f": first, "s": last},
        )
    # One login acting for both.
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": fx.contacts})},
    )

    async def call(contact_id: int | None) -> dict[str, object]:
        return await api.my_onboarding(
            contact_id=contact_id,
            principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
            ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
        )

    def subjects(out: dict[str, object]) -> set[object]:
        steps = out["steps"]
        assert isinstance(steps, list)
        return {s["subject_name"] for s in steps} - {None}

    def you_name(out: dict[str, object]) -> object:
        review = out["review"]
        assert isinstance(review, dict)
        you = review["you"]
        assert isinstance(you, dict)
        return you["name"]

    both = await call(None)
    assert {"Alice Able", "Bob Baker"} <= subjects(both)

    alice = await call(fx.contacts[0])
    assert "Bob Baker" not in subjects(alice)
    assert you_name(alice) == "Alice Able"

    bob = await call(fx.contacts[1])
    assert "Alice Able" not in subjects(bob)
    assert you_name(bob) == "Bob Baker"

    # NARROWS ONLY. An id the caller does not actually hold — a stale cookie, a tampered value —
    # is ignored and the answer falls back to the whole scope. It can never reach a contact
    # outside it.
    outsider = await call(max(fx.contacts) + 10_000)
    assert {"Alice Able", "Bob Baker"} <= subjects(outsider)


def test_review_helpers_hide_empty_address_and_bank() -> None:
    assert api._address_out(None) is None
    assert api._address_out({}) is None
    assert api._address_out({"line1": "  ", "city": ""}) is None
    assert api._address_out({"line1": "1 Road", "city": "Bath"}) == {
        "line1": "1 Road",
        "city": "Bath",
    }
    assert api._bank_out(
        {"name": "A", "sort_code": "11-22-33", "account_number": "00001111", "bank": "X"}
    ) == {
        "name": "A",
        "sort_code": "11-22-33",
        "account_number": "00001111",
    }
    assert api._pick_role(["beneficiary", "owner"]) == "owner"
    assert (
        api._contact_display_name({"first_name": "A", "surname": "B", "name": "Org", "email": "e"})
        == "A B"
    )


def test_review_helpers_surface_kickoff_account_richness() -> None:
    """Safe transfer/JISA/beneficiary projections — ids and junk keys stay out."""
    assert api._transfer_summary(None) is None
    assert api._transfer_summary({"intent": True}) is None
    assert api._transfer_summary(
        {
            "intent": True,
            "transfer_type": "in-specie",
            "amount": "2500",
            "provider_id": 42,
            "provider_name": "Ceding Co",
            "secret": "nope",
        }
    ) == {"type": "in_specie", "amount": 2500.0, "provider_name": "Ceding Co"}
    assert api._transfer_summary({"extent": "partial", "transfer_type": "cash"}) == {
        "type": "cash",
        "extent": "partial",
    }
    assert api._transfer_summary(
        {
            "transfer_type": "cash",
            "safeguarded_benefits": True,
            "fca_reference": " 123456 ",
            "provider_id": 7,
        }
    ) == {"type": "cash", "safeguarded_benefits": True, "fca_reference": "123456"}
    assert api._transfer_summary({"transfer_type": "cash", "safeguarded_benefits": False}) == {
        "type": "cash",
        "safeguarded_benefits": False,
    }
    assert api._jisa_child_out(None) is None
    assert api._jisa_child_out({}) is None
    assert api._jisa_child_out({"jisa_child": {"name": "  ", "dob": ""}}) is None
    assert api._jisa_child_out({"jisa_child": {"name": "Sam", "dob": "2018-06-15"}}) == {
        "name": "Sam",
        "dob": "2018-06-15",
    }
    assert api._beneficiaries_out({"beneficiaries": []}) is None
    assert api._beneficiaries_out(
        {
            "beneficiaries": [
                {"first_name": "Pat", "surname": "Able", "portion": 60, "extra": 1},
                {"first_name": " ", "surname": ""},
            ]
        }
    ) == [{"first_name": "Pat", "surname": "Able", "portion": 60.0}]
    assert api._regular_contribution_out(None) is None
    assert api._regular_contribution_out({}) is None
    assert api._regular_contribution_out({"regular_contribution": {"intent": False}}) is None
    assert api._regular_contribution_out(
        {
            "regular_contribution": {
                "intent": True,
                "amount": "1000",
                "frequency": "monthly",
                "expected_by": "2026-09-01",
            }
        }
    ) == {
        "intent": True,
        "amount": 1000.0,
        "frequency": "monthly",
        "expected_by": "2026-09-01",
    }


# ==================================================================================================
# /applications/me -- what is actually the CLIENT's to do now
# ==================================================================================================
def _step(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "pending",
        "step_type": "accept_terms",
        "item_uid": "itm_1",
        "awaiting": None,
    }
    return base | over


class TestClientCanAct:
    """The rule behind the skip-ahead defect: the client portal offered EVERY pending step of
    theirs, so all five terms declarations appeared with live "Review & agree" buttons while the
    adviser's fact-find, identity and AML were outstanding. The first of them is held on a STAFF
    step, so the flow already said it was not their turn."""

    def test_a_step_held_on_a_prerequisite_is_not_theirs_yet(self) -> None:
        assert api._client_can_act(_step(awaiting=["activity"])) is False

    def test_a_request_backed_step_with_no_artifact_is_not_theirs_yet(self) -> None:
        # Nothing exists to open, and `_assert_fulfilment_supported` would refuse the submission.
        assert api._client_can_act(_step(item_uid=None)) is False

    def test_a_settled_step_is_nobodys_to_do(self) -> None:
        for status_ in ("fulfilled", "skipped", "cancelled", "failed"):
            assert api._client_can_act(_step(status=status_)) is False

    def test_an_open_unheld_step_with_its_artifact_IS_theirs(self) -> None:
        assert api._client_can_act(_step()) is True
        assert api._client_can_act(_step(status="waiting")) is True

    def test_a_step_that_needs_no_artifact_is_not_held_on_one(self) -> None:
        # A `task` completes by being done, not by producing a document — absent item_uid is normal.
        assert api._client_can_act(_step(step_type="task", item_uid=None)) is True

    def test_identity_photos_with_the_check_are_not_theirs_to_send_again(self) -> None:
        def idv(capture_status: str | None) -> dict[str, Any]:
            return _step(
                step_type="verify_identity",
                status="waiting",
                item_uid=None,
                capture_status=capture_status,
            )

        assert api._client_can_act(idv("submitted")) is False
        assert api._client_can_act(idv("review")) is False
        # Still to take, or asked for again: theirs.
        assert api._client_can_act(idv(None)) is True
        assert api._client_can_act(idv("capturing")) is True
        assert api._client_can_act(idv("retake")) is True


@pytest.mark.asyncio
async def test_my_onboarding_marks_held_client_steps_unavailable(
    service_session: AsyncSession,
) -> None:
    """End to end against the real engine: a client-assigned step sitting behind a staff step comes
    back `available: false`, and `available` counts only what they can genuinely act on."""
    sess = service_session
    fx = await _seed(
        sess,
        steps=[
            {
                "id": "core",
                "type": "fill_form",
                "title": "Applicant details",
                "scope": "participant",
                "assignee": "staff",
                "config": {"form_uid": "__FORM_UID__"},
            },
            {
                "id": "terms_general",
                "type": "accept_terms",
                "title": "General declaration",
                "scope": "participant",
                "assignee": "client",
                "depends_on": ["core"],
                "config": {"document_uid": "doc_x", "title": "General declaration"},
            },
        ],
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    p = Principal(user_id="wos_client", organisation_id="org_1", role="client")
    out = await api.my_onboarding(
        principal=p,
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
    )
    assert out["total"] == 1, "the client's own journey still states its full length"
    assert out["available"] == 0, "but nothing is theirs to do while the adviser's step is open"
    assert out["steps"][0]["available"] is False


@pytest.mark.asyncio
async def test_my_onboarding_names_the_catalogue_term_a_declaration_pins(
    service_session: AsyncSession,
) -> None:
    """The client portal lists the firm's outstanding catalogue terms beside the journey's own
    declarations, and leaves out the ones a declaration already collects. It can only do that if
    each step says which term it pins — the filed target document carries it, and this is the
    read. A declaration that pins no term answers null, not a title the page would have to guess
    from."""
    sess = service_session
    fx = await _seed(
        sess,
        steps=[
            {
                "id": "terms_general",
                "type": "accept_terms",
                "title": "General declaration",
                "scope": "participant",
                "assignee": "client",
                "config": {"content": "<p>Agree.</p>", "title": "General declaration"},
            },
        ],
    )
    await sess.execute(
        text(
            'UPDATE documents.documents d SET metadata = d.metadata || \'{"term_uid": "trm1"}\' '
            "FROM documents.request_items ri JOIN flows.case_steps cs "
            "  ON cs.request_item_id = ri.id "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE d.id = ri.target_document_id AND c.execution_id = :e"
        ),
        {"e": fx.execution_id},
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    out = await api.my_onboarding(
        principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
    )
    assert [s["term_uid"] for s in out["steps"]] == ["trm1"]
