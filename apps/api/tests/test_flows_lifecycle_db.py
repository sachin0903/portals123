"""Integration tests for the orchestration lifecycle gaps the functional audit found, against a real
Postgres (testcontainer). Each of these was a hard product dead end:

  * **``depends_on`` was decorative** — validated, planned, persisted and read by nothing, so every
    step instantiated at once: the client was handed an identity step (and a ``crm.kyc_checks``
    pending row was opened) before the fact-find was touched. Now a held step gets NO artifact until
    its prerequisites settle, and recompute releases it.
  * **``generate_document`` enqueued nothing** — it returned WAITING with no document and no render
    job, so any flow containing one was stuck forever with no staff override. Now it creates the
    document + render job and the worker's callback completes (or fails) the step.
  * **Nothing could cancel a journey** — ``CANCELLED`` was unreachable, and
    ``cases_one_active_per_subject`` then locked that client out of re-onboarding permanently.
  * **A failed step pinned the case** with no retry.

Needs Docker (service_session fixture → PostgresContainer); skipped otherwise.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import orchestrator
from app.flows.domain_events import StepEvent, StepSignal
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from tests.conftest import SECCL_PLATFORM_SQL


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _step(sess: AsyncSession, execution_id: int, key: str, participant: int | None = None):
    return (
        (
            await sess.execute(
                text(
                    "SELECT cs.id, cs.case_id, cs.status, cs.request_item_id, cs.data "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.step_key = :k "
                    "  AND cs.participant_subject_id IS NOT DISTINCT FROM :p"
                ),
                {"e": execution_id, "k": key, "p": participant},
            )
        )
        .mappings()
        .one()
    )


class _Fixture:
    """A tenant + application + one individual owner + one account, and a helper to start a flow."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _seed(sess: AsyncSession, definition: dict[str, Any]) -> _Fixture:
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"lc_{s}",
            a=f"lc-{s}",
            n=f"LC {s}",
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
    contact = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
            "VALUES (:t, :u, 'individual', :e) RETURNING id",
            t=tenant,
            u=f"c_{s}",
            e=f"c_{s}@t.test",
        )
    )
    account = int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
            "VALUES (:t, :c, :a, 'draft', :u) RETURNING id",
            t=tenant,
            c=contact,
            a=app_id,
            u=f"acc_{s}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": tenant, "a": app_id, "acc": account, "c": contact},
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
            "  label, sort_order) VALUES (:u, :t, :f, 'first_name', 'text', 'First name', 1)"
        ),
        {"u": f"fld_{s}", "t": tenant, "f": form_id},
    )
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :code, 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
            code=f"onb_{s}",
        )
    )
    filled = json.dumps(definition).replace("__FORM_UID__", form_uid)
    definition = json.loads(filled)
    vid = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{s}",
            t=tenant,
            f=flow_id,
            d=filled,
        )
    )
    return _Fixture(
        suffix=s,
        tenant=tenant,
        platform=platform,
        app_id=app_id,
        contact=contact,
        account=account,
        flow_id=flow_id,
        version_id=vid,
        form_uid=form_uid,
        definition=definition,
        graph=ApplicationGraph(
            participants=(Participant(contact, "individual", frozenset({"owner"})),),
            accounts=(AccountSpec(account, (contact,)),),
        ),
    )


async def _start(sess: AsyncSession, fx: _Fixture) -> int:
    created = await orchestrator.start_execution(
        sess,
        tenant_id=fx.tenant,
        flow_id=fx.flow_id,
        flow_version_id=fx.version_id,
        definition=fx.definition,
        graph=fx.graph,
        application_id=fx.app_id,
        data={"adviser_declared": True},
    )
    return int(created["id"])


# ==================================================================================================
# depends_on
# ==================================================================================================
_ORDERED = {
    "steps": [
        {
            "id": "core",
            "type": "fill_form",
            "title": "Applicant details",
            "scope": "participant",
            "assignee": "staff",
            "config": {"form_uid": "__FORM_UID__"},
        },
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "assignee": "staff",
            "depends_on": ["core"],
            "config": {},
        },
    ],
    "milestones": [],
}


@pytest.mark.asyncio
async def test_dependent_step_gets_no_artifact_until_its_prerequisite_settles(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, _ORDERED)
    execution_id = await _start(sess, fx)

    core = await _step(sess, execution_id, "core", fx.contact)
    idv = await _step(sess, execution_id, "idv", fx.contact)

    # 'core' instantiated: it has its request item. 'idv' is HELD — no pending crm.kyc_checks row
    # exists yet, and the outstanding prerequisite is recorded for the UI.
    assert core["request_item_id"] is not None
    assert idv["status"] == "pending"
    assert idv["data"].get("awaiting") == ["core"]
    opened = await _scalar(
        sess,
        "SELECT count(*) FROM crm.kyc_checks WHERE tenant_id = :t AND contact_id = :c",
        t=fx.tenant,
        c=fx.contact,
    )
    assert int(opened) == 0, "identity check must not be opened before the fact-find"

    # Settle 'core' → the next recompute releases 'idv', which opens its check and waits.
    await orchestrator.apply_step_event(
        sess,
        fx.tenant,
        StepEvent(
            StepSignal.COMPLETED, int(core["case_id"]), "core", participant_subject_id=fx.contact
        ),
    )
    idv = await _step(sess, execution_id, "idv", fx.contact)
    assert idv["status"] == "waiting"
    assert "awaiting" not in idv["data"]
    opened = await _scalar(
        sess,
        "SELECT count(*) FROM crm.kyc_checks WHERE tenant_id = :t AND contact_id = :c "
        "  AND status = 'pending'",
        t=fx.tenant,
        c=fx.contact,
    )
    assert int(opened) == 1


@pytest.mark.asyncio
async def test_release_is_idempotent_across_recomputes(service_session: AsyncSession) -> None:
    """A released step must not be instantiated twice — a second recompute would otherwise open a
    second pending KYC check for the same person."""
    sess = service_session
    fx = await _seed(sess, _ORDERED)
    execution_id = await _start(sess, fx)
    core = await _step(sess, execution_id, "core", fx.contact)
    await orchestrator.apply_step_event(
        sess,
        fx.tenant,
        StepEvent(
            StepSignal.COMPLETED, int(core["case_id"]), "core", participant_subject_id=fx.contact
        ),
    )
    await orchestrator.recompute(sess, execution_id, tenant_id=fx.tenant)
    await orchestrator.recompute(sess, execution_id, tenant_id=fx.tenant)
    checks = await _scalar(
        sess,
        "SELECT count(*) FROM crm.kyc_checks WHERE tenant_id = :t AND contact_id = :c",
        t=fx.tenant,
        c=fx.contact,
    )
    assert int(checks) == 1


_JOINT_ORDER = {
    "steps": [
        {
            "id": "terms",
            "type": "accept_terms",
            "title": "Accept the terms",
            "scope": "account",
            "per_participant": True,
            "assignee": "client",
            "config": {"document_uid": "doc_terms_missing"},
        },
        {
            "id": "setup",
            "type": "setup_account",
            "title": "Open the account",
            "scope": "account",
            "assignee": "staff",
            "depends_on": ["terms"],
            "config": {},
        },
    ],
    "milestones": [],
}


@pytest.mark.asyncio
async def test_a_shared_step_waits_for_every_owners_fanned_prerequisite(
    service_session: AsyncSession,
) -> None:
    """'Open the account once BOTH owners have accepted the terms' — a shared step depending on a
    per-participant one.

    The dependency resolver used to consider only facts whose participant matched the dependent's
    (None for a shared step), so every fanned sibling was skipped, the key never entered the map and
    the step released at kickoff. The joint-account rule the editor happily lets you author was
    silently ignored at runtime.
    """
    sess = service_session
    fx = await _seed(sess, _JOINT_ORDER)
    second = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
            "VALUES (:t, :u, 'individual', :e) RETURNING id",
            t=fx.tenant,
            u=f"c2_{fx.suffix}",
            e=f"c2_{fx.suffix}@t.test",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": fx.tenant, "a": fx.app_id, "acc": fx.account, "c": second},
    )
    fx.graph = ApplicationGraph(
        participants=(
            Participant(fx.contact, "individual", frozenset({"owner"})),
            Participant(second, "individual", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(fx.account, (fx.contact, second)),),
    )
    execution_id = await _start(sess, fx)

    setup = await _step(sess, execution_id, "setup")
    assert setup["data"].get("awaiting") == ["terms"], (
        "the shared step must be held until EVERY owner's terms instance settles"
    )
    assert setup["data"].get("instantiated") is not True

    # One owner accepts: still held, because the other has not.
    first_terms = await _step(sess, execution_id, "terms", fx.contact)
    await orchestrator.apply_step_event(
        sess,
        fx.tenant,
        StepEvent(
            StepSignal.COMPLETED,
            int(first_terms["case_id"]),
            "terms",
            participant_subject_id=fx.contact,
        ),
    )
    setup = await _step(sess, execution_id, "setup")
    assert setup["data"].get("awaiting") == ["terms"]
    assert setup["data"].get("instantiated") is not True

    # The last owner accepts → released.
    await orchestrator.apply_step_event(
        sess,
        fx.tenant,
        StepEvent(
            StepSignal.COMPLETED,
            int(first_terms["case_id"]),
            "terms",
            participant_subject_id=second,
        ),
    )
    setup = await _step(sess, execution_id, "setup")
    assert setup["data"].get("instantiated") is True
    assert "awaiting" not in setup["data"]


_ACCOUNT_SCOPE_FORM = {
    "steps": [
        {
            "id": "activity",
            "type": "fill_form",
            "title": "Account activity",
            "scope": "account",
            "assignee": "staff",
            "config": {"form_uid": "__FORM_UID__"},
        }
    ],
    "milestones": [],
}


@pytest.mark.asyncio
async def test_an_account_scoped_form_binds_to_the_accounts_primary_owner(
    service_session: AsyncSession,
) -> None:
    """The seeded 'Account activity' step: a ``fill_form`` at ACCOUNT scope with no
    ``per_participant``, so no participant was resolved, no request item was created and it sat
    ``pending`` with no actor — a silent dead end. It now binds to the account's primary owner."""
    sess = service_session
    fx = await _seed(sess, _ACCOUNT_SCOPE_FORM)
    execution_id = await _start(sess, fx)

    step = await _step(sess, execution_id, "activity")
    assert step["request_item_id"] is not None, "no artifact ⇒ nobody can ever complete the step"
    bound = await _scalar(
        sess,
        "SELECT r.contact_id FROM documents.request_items ri "
        "JOIN documents.requests r ON r.id = ri.request_id WHERE ri.id = :i",
        i=int(step["request_item_id"]),
    )
    assert int(bound) == fx.contact
    assert (
        await _scalar(
            sess,
            "SELECT form_uid FROM documents.request_items WHERE id = :i",
            i=int(step["request_item_id"]),
        )
        == fx.form_uid
    )


# ==================================================================================================
# generate_document
# ==================================================================================================
async def _seed_template(sess: AsyncSession, fx: _Fixture) -> str:
    uid = f"tpl_{fx.suffix}"
    author = await _scalar(
        sess,
        "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
        u=f"usr_{fx.suffix}",
        t=fx.tenant,
        e=f"author_{fx.suffix}@t.test",
    )
    tpl = int(
        await _scalar(
            sess,
            "INSERT INTO documents.templates "
            "  (uid, tenant_id, code, name, document_type, created_by) "
            "VALUES (:u, :t, :c, 'Suitability pack', 'suitability', :by) RETURNING id",
            u=uid,
            t=fx.tenant,
            c=f"tc_{fx.suffix}",
            by=author,
        )
    )
    vid = int(
        await _scalar(
            sess,
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, created_by) "
            "VALUES (:u, :t, :tpl, 1, 'published', :by) RETURNING id",
            u=f"tv_{fx.suffix}",
            t=fx.tenant,
            tpl=tpl,
            by=author,
        )
    )
    await sess.execute(
        text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :i"),
        {"v": vid, "i": tpl},
    )
    return uid


@pytest.mark.asyncio
async def test_generate_document_enqueues_a_render_and_the_callback_completes_the_step(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, {"steps": [], "milestones": []})
    template_uid = await _seed_template(sess, fx)
    definition = {
        "steps": [
            {
                "id": "pack",
                "type": "generate_document",
                "title": "Suitability pack",
                "scope": "application",
                "assignee": "staff",
                "config": {"template_uid": template_uid},
            }
        ],
        "milestones": [],
    }
    await sess.execute(
        text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
        {"d": json.dumps(definition), "i": fx.version_id},
    )
    fx.definition = definition
    execution_id = await _start(sess, fx)

    step = await _step(sess, execution_id, "pack")
    assert step["status"] == "waiting"
    document_id = step["data"].get("document_id")
    assert document_id, "the step must record the document it enqueued"
    queued = await _scalar(
        sess,
        "SELECT count(*) FROM documents.render_jobs WHERE document_id = CAST(:d AS uuid) "
        "  AND status = 'queued'",
        d=document_id,
    )
    assert int(queued) == 1, "a render job must be enqueued — otherwise the step waits forever"

    # The worker's callback closes the loop.
    await orchestrator.on_document_rendered(sess, document_id)
    assert (await _step(sess, execution_id, "pack"))["status"] == "fulfilled"


@pytest.mark.asyncio
async def test_render_failure_fails_the_step_and_can_be_retried(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, {"steps": [], "milestones": []})
    template_uid = await _seed_template(sess, fx)
    definition = {
        "steps": [
            {
                "id": "pack",
                "type": "generate_document",
                "title": "Suitability pack",
                "scope": "application",
                "assignee": "staff",
                "config": {"template_uid": template_uid},
            }
        ],
        "milestones": [],
    }
    await sess.execute(
        text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
        {"d": json.dumps(definition), "i": fx.version_id},
    )
    fx.definition = definition
    execution_id = await _start(sess, fx)
    step = await _step(sess, execution_id, "pack")

    await orchestrator.on_document_rendered(sess, step["data"]["document_id"], error="boom")
    failed = await _step(sess, execution_id, "pack")
    assert failed["status"] == "failed"
    assert "render_failed" in str(failed["data"].get("last_event"))

    # Retry re-enqueues a fresh render rather than leaving the case pinned.
    assert await orchestrator.retry_step(
        sess, tenant_id=fx.tenant, case_id=int(failed["case_id"]), step_key="pack"
    )
    retried = await _step(sess, execution_id, "pack")
    assert retried["status"] == "waiting"
    assert retried["data"]["document_id"] != step["data"]["document_id"]


@pytest.mark.asyncio
async def test_generate_document_without_a_published_template_fails_loudly(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, {"steps": [], "milestones": []})
    definition = {
        "steps": [
            {
                "id": "pack",
                "type": "generate_document",
                "title": "Suitability pack",
                "scope": "application",
                "assignee": "staff",
                "config": {"template_uid": "tpl_missing"},
            }
        ],
        "milestones": [],
    }
    await sess.execute(
        text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
        {"d": json.dumps(definition), "i": fx.version_id},
    )
    fx.definition = definition
    execution_id = await _start(sess, fx)
    step = await _step(sess, execution_id, "pack")
    assert step["status"] == "failed"  # not a silent eternal WAITING
    assert step["data"]["last_event"] == "template_not_published"


# ==================================================================================================
# cancellation + the one-active-per-subject guard
# ==================================================================================================
_SIMPLE = {
    "steps": [
        {
            "id": "core",
            "type": "fill_form",
            "title": "Applicant details",
            "scope": "participant",
            "assignee": "client",
            "config": {"form_uid": "__FORM_UID__"},
        }
    ],
    "milestones": [],
}


@pytest.mark.asyncio
async def test_second_journey_for_the_same_subject_is_refused_not_a_500(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    first = await _start(sess, fx)
    with pytest.raises(orchestrator.ActiveExecutionExists) as refused:
        await _start(sess, fx)
    # The refusal names the journey in the way: the wizard links to it and offers to stop it.
    assert refused.value.execution_uid == await _scalar(
        sess, "SELECT uid FROM flows.executions WHERE id = :e", e=first
    )


@pytest.mark.asyncio
async def test_a_lost_start_race_is_refused_not_a_500(service_session: AsyncSession) -> None:
    """The readable pre-check is not a lock: two advisers clicking "start" at the same instant both
    see no live case (neither transaction has committed) and the second INSERT loses on
    ``cases_one_active_per_subject``. Simulated by blinding the pre-check — which is what a
    concurrent uncommitted sibling does to it — so the *index* is the guard being tested. Before
    the mapping this surfaced as a raw IntegrityError → 500.
    """
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    await _start(sess, fx)
    original = orchestrator.live_execution_uid

    async def _blind(*_args: Any, **_kw: Any) -> str | None:
        return None

    orchestrator.live_execution_uid = _blind  # type: ignore[assignment]
    try:
        with pytest.raises(orchestrator.ActiveExecutionExists) as refused:
            # A savepoint, so the integrity error unwinds without poisoning the test's transaction.
            async with sess.begin_nested():
                await _start(sess, fx)
        # The index caught what the pre-check could not see, so there is no uid to offer.
        assert refused.value.execution_uid is None
    finally:
        orchestrator.live_execution_uid = original  # type: ignore[assignment]
    # And the winner is untouched: still exactly one live case for the subject.
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM flows.cases WHERE flow_id = :f AND subject_contact_id = :c "
                "  AND completed_at IS NULL AND cancelled_at IS NULL",
                f=fx.flow_id,
                c=fx.contact,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_cancel_withdraws_the_work_and_frees_the_subject(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    execution_id = await _start(sess, fx)
    step = await _step(sess, execution_id, "core", fx.contact)
    item_id = int(step["request_item_id"])

    assert await orchestrator.cancel_execution(
        sess, tenant_id=fx.tenant, execution_id=execution_id, reason="started in error"
    )

    assert (
        await _scalar(sess, "SELECT status FROM flows.executions WHERE id = :e", e=execution_id)
        == "cancelled"
    )
    assert (await _step(sess, execution_id, "core", fx.contact))["status"] == "cancelled"
    # The client's checklist clears: the outstanding request item is withdrawn.
    assert (
        await _scalar(sess, "SELECT status FROM documents.request_items WHERE id = :i", i=item_id)
        == "cancelled"
    )
    # …and the subject can be re-onboarded, which the unique index blocked before.
    await _start(sess, fx)


@pytest.mark.asyncio
async def test_cancel_leaves_an_already_fulfilled_request_alone(
    service_session: AsyncSession,
) -> None:
    """A contact who had already completed every item ('fulfilled') had that request relabelled
    'cancelled' when the journey was stopped for an unrelated outstanding step — rewriting a record
    of work the client genuinely did. Only requests this cancel actually withdrew from may change.
    """
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    second = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
            "VALUES (:t, :u, 'individual', :e) RETURNING id",
            t=fx.tenant,
            u=f"c2_{fx.suffix}",
            e=f"c2_{fx.suffix}@t.test",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": fx.tenant, "a": fx.app_id, "acc": fx.account, "c": second},
    )
    fx.graph = ApplicationGraph(
        participants=(
            Participant(fx.contact, "individual", frozenset({"owner"})),
            Participant(second, "individual", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(fx.account, (fx.contact, second)),),
    )
    execution_id = await _start(sess, fx)

    # Owner one finishes everything they were asked for.
    done_item = int((await _step(sess, execution_id, "core", fx.contact))["request_item_id"])
    done_request = int(
        await _scalar(
            sess, "SELECT request_id FROM documents.request_items WHERE id = :i", i=done_item
        )
    )
    await sess.execute(
        text(
            "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now() "
            "WHERE id = :i"
        ),
        {"i": done_item},
    )
    await sess.execute(
        text("UPDATE documents.requests SET status = 'fulfilled' WHERE id = :r"),
        {"r": done_request},
    )
    outstanding_request = int(
        await _scalar(
            sess,
            "SELECT ri.request_id FROM documents.request_items ri WHERE ri.id = :i",
            i=int((await _step(sess, execution_id, "core", second))["request_item_id"]),
        )
    )

    await orchestrator.cancel_execution(
        sess, tenant_id=fx.tenant, execution_id=execution_id, reason="owner two went quiet"
    )

    assert (
        await _scalar(sess, "SELECT status FROM documents.requests WHERE id = :r", r=done_request)
        == "fulfilled"
    ), "a completed request must not be rewritten as cancelled"
    assert (
        await _scalar(
            sess, "SELECT status FROM documents.requests WHERE id = :r", r=outstanding_request
        )
        == "cancelled"
    )


@pytest.mark.asyncio
async def test_cancel_is_idempotent(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    execution_id = await _start(sess, fx)
    assert await orchestrator.cancel_execution(sess, tenant_id=fx.tenant, execution_id=execution_id)
    assert not await orchestrator.cancel_execution(
        sess, tenant_id=fx.tenant, execution_id=execution_id
    )


@pytest.mark.asyncio
async def test_cancel_voids_the_pending_kyc_check(service_session: AsyncSession) -> None:
    """An abandoned journey must not leave an eternal `pending` check that a LATER journey's outcome
    could attach itself to."""
    sess = service_session
    fx = await _seed(
        sess,
        {
            "steps": [
                {
                    "id": "idv",
                    "type": "verify_identity",
                    "title": "Verify identity",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                }
            ],
            "milestones": [],
        },
    )
    execution_id = await _start(sess, fx)
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM crm.kyc_checks WHERE contact_id = :c AND status = 'pending'",
                c=fx.contact,
            )
        )
        == 1
    )
    await orchestrator.cancel_execution(sess, tenant_id=fx.tenant, execution_id=execution_id)
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM crm.kyc_checks WHERE contact_id = :c AND status = 'pending'",
                c=fx.contact,
            )
        )
        == 0
    )


_IDV_ONLY = {
    "steps": [
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "assignee": "staff",
            "config": {},
        }
    ],
    "milestones": [],
}


async def _sibling_journey(
    sess: AsyncSession, fx: _Fixture, definition: dict[str, Any]
) -> _Fixture:
    """A SECOND journey over the same tenant + the same person: a new flow (so
    ``cases_one_active_per_subject``, keyed on flow_id, allows it), its own application and account.

    This is the real shape behind the KYC cross-write: one client, two live journeys — a second
    account opened while the first onboarding is still in flight.
    """
    s = uuid.uuid4().hex[:8]
    app_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
            "VALUES (:t, :p, :u, 'draft') RETURNING id",
            t=fx.tenant,
            p=fx.platform,
            u=f"app2_{s}",
        )
    )
    account = int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
            "VALUES (:t, :c, :a, 'draft', :u) RETURNING id",
            t=fx.tenant,
            c=fx.contact,
            a=app_id,
            u=f"acc2_{s}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": fx.tenant, "a": app_id, "acc": account, "c": fx.contact},
    )
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :code, 'Second flow', 'published') RETURNING id",
            u=f"flw2_{s}",
            t=fx.tenant,
            code=f"onb2_{s}",
        )
    )
    filled = json.dumps(definition).replace("__FORM_UID__", fx.form_uid)
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv2_{s}",
            t=fx.tenant,
            f=flow_id,
            d=filled,
        )
    )
    return _Fixture(
        suffix=s,
        tenant=fx.tenant,
        platform=fx.platform,
        app_id=app_id,
        contact=fx.contact,
        account=account,
        flow_id=flow_id,
        version_id=version_id,
        form_uid=fx.form_uid,
        definition=json.loads(filled),
        graph=ApplicationGraph(
            participants=(Participant(fx.contact, "individual", frozenset({"owner"})),),
            accounts=(AccountSpec(account, (fx.contact,)),),
        ),
    )


@pytest.mark.asyncio
async def test_an_outcome_lands_on_the_check_its_own_step_opened(
    service_session: AsyncSession,
) -> None:
    """Two live journeys for the SAME person (a second account opened while the first is in flight,
    allowed because the one-active guard is per flow) each open their own ``crm.kyc_checks`` row.

    The outcome used to be written to "the latest pending check for this (tenant, contact, kind)",
    so an identity outcome on journey 1 stamped journey 2's check instead: one client's verdict
    landing on the wrong application. The step now records which check it opened and updates that
    row.

    The outcome here arrives the only way a manual identity check's can: its two documents land
    (``on_item_fulfilled``), and the engine completes the step. Nobody types a verdict on an
    identity check — ``submit_step`` refuses one — so a hand-recorded REFERRED is no longer a
    way to drive this.
    """
    sess = service_session
    first = await _seed(sess, _IDV_ONLY)
    exec_one = await _start(sess, first)
    check_one = int(
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.kyc_checks WHERE contact_id = :c AND status = 'pending'",
            c=first.contact,
        )
    )
    assert check_one == 1

    # A second journey over the same person, started AFTER the first: its check is the newest
    # pending one — the row the old "latest pending" update would have hit.
    second = await _sibling_journey(sess, first, _IDV_ONLY)
    exec_two = await _start(sess, second)
    ids = [
        int(r)
        for r in (
            await sess.execute(
                text(
                    "SELECT id FROM crm.kyc_checks WHERE contact_id = :c AND status = 'pending' "
                    "ORDER BY id"
                ),
                {"c": first.contact},
            )
        )
        .scalars()
        .all()
    ]
    assert len(ids) == 2, "each journey opens its own check"

    # Land BOTH identity documents on the FIRST journey's step, which settles it.
    step = await _step(sess, exec_one, "idv", first.contact)
    assert step["data"].get("kyc_check_id") == ids[0]
    recorded = step["data"].get("identity_documents")
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    assert isinstance(recorded, dict) and len(recorded) == 2, "the manual route raised both"
    item_ids = [
        int(r)
        for r in (
            await sess.execute(
                text("SELECT id FROM documents.request_items WHERE uid = ANY(:u) ORDER BY id"),
                {"u": [str(uid) for uid in recorded.values()]},
            )
        )
        .scalars()
        .all()
    ]
    for item_id in item_ids:
        await sess.execute(
            text(
                "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now() "
                "WHERE id = :i"
            ),
            {"i": item_id},
        )
        await orchestrator.on_item_fulfilled(sess, item_id)
    rows = (
        await sess.execute(
            text("SELECT id, status FROM crm.kyc_checks WHERE contact_id = :c"),
            {"c": first.contact},
        )
    ).all()
    statuses = {int(r[0]): str(r[1]) for r in rows}
    assert statuses[ids[0]] == "passed", "the outcome belongs to the step that opened the check"
    assert statuses[ids[1]] == "pending", "the other journey's check must be untouched"
    assert (await _step(sess, exec_one, "idv", first.contact))["status"] == "fulfilled"
    # And the second journey's own step is still waiting on its own check.
    assert (await _step(sess, exec_two, "idv", first.contact))["status"] == "waiting"


@pytest.mark.asyncio
async def test_cancelling_one_journey_leaves_the_others_check_alone(
    service_session: AsyncSession,
) -> None:
    """The compensation half of the same bug: cancelling journey 1 used to expire whichever pending
    check was newest, which is journey 2's."""
    sess = service_session
    first = await _seed(sess, _IDV_ONLY)
    exec_one = await _start(sess, first)
    second = await _sibling_journey(sess, first, _IDV_ONLY)
    exec_two = await _start(sess, second)
    step_two = await _step(sess, exec_two, "idv", first.contact)
    kept = int(step_two["data"]["kyc_check_id"])

    await orchestrator.cancel_execution(sess, tenant_id=first.tenant, execution_id=exec_one)
    assert (
        str(await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :i", i=kept))
        == "pending"
    )
    assert (
        int(
            await _scalar(
                sess,
                "SELECT count(*) FROM crm.kyc_checks WHERE contact_id = :c AND status = 'expired'",
                c=first.contact,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_cancel_cancels_the_journeys_open_tasks(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    execution_id = await _start(sess, fx)
    exec_uid = await _scalar(sess, "SELECT uid FROM flows.executions WHERE id = :e", e=execution_id)
    role_id = int(
        await _scalar(
            sess,
            "INSERT INTO auth.roles (tenant_id, slug, name) "
            "VALUES (:t, 'compliance', 'Compliance') "
            "RETURNING id",
            t=fx.tenant,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO tasks.tasks (uid, tenant_id, subject_type, subject_id, assignee_kind, "
            "  assignee_role_id, title) VALUES (:u, :t, 'execution', :s, 'role', :r, 'Chase')"
        ),
        {"u": f"tsk_{fx.suffix}", "t": fx.tenant, "s": exec_uid, "r": role_id},
    )
    await orchestrator.cancel_execution(sess, tenant_id=fx.tenant, execution_id=execution_id)
    assert (
        await _scalar(sess, "SELECT status FROM tasks.tasks WHERE uid = :u", u=f"tsk_{fx.suffix}")
        == "cancelled"
    )


# ==================================================================================================
# audit spine
# ==================================================================================================
@pytest.mark.asyncio
async def test_replaying_a_transition_appends_one_fact_not_two(
    service_session: AsyncSession,
) -> None:
    """``idempotency_key`` used to be a fresh uuid per call, so the unique index never fired and a
    retried transition double-counted in the journey timeline."""
    sess = service_session
    fx = await _seed(sess, _SIMPLE)
    execution_id = await _start(sess, fx)
    step = await _step(sess, execution_id, "core", fx.contact)
    for _ in range(2):
        await orchestrator.apply_step_event(
            sess,
            fx.tenant,
            StepEvent(
                StepSignal.COMPLETED,
                int(step["case_id"]),
                "core",
                participant_subject_id=fx.contact,
            ),
        )
    facts = await _scalar(
        sess,
        "SELECT count(*) FROM flows.domain_events WHERE execution_id = :e "
        "  AND event_type = 'step.completed'",
        e=execution_id,
    )
    assert int(facts) == 1


@pytest.mark.asyncio
async def test_a_review_decision_records_its_note_and_decider(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(
        sess,
        {
            "steps": [
                {
                    "id": "edd",
                    "type": "review",
                    "title": "Offshore review",
                    "scope": "account",
                    "assignee": "staff",
                    "config": {"gate": "offshore_approved"},
                }
            ],
            "milestones": [],
        },
    )
    execution_id = await _start(sess, fx)
    step = await _step(sess, execution_id, "edd")
    await orchestrator.submit_step(
        sess,
        tenant_id=fx.tenant,
        case_id=int(step["case_id"]),
        step_key="edd",
        detail={"outcome": "passed", "note": "EDD file reviewed, source of wealth evidenced."},
    )
    done = await _step(sess, execution_id, "edd")
    assert done["status"] == "fulfilled"
    assert done["data"]["note"].startswith("EDD file reviewed")
    assert done["data"]["outcome"] == "passed"
