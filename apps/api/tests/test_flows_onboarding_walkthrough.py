"""Drive the REAL seeded onboarding flow to `completed` using only staff-reachable endpoints.

This is the demo, as a test. The functional audit's headline finding was that the seeded flow could
not be finished at all; the fix was partly a fulfilment surface and partly the actor split (a client
step nobody could complete blocked everything behind it). Neither is safe from regression unless
something actually walks the whole graph — a per-step unit test passes happily while the *sequence*
is impassable.

So this seeds the flow exactly as `provisioning` does, starts it over a SIPP application, and then
completes every step through the same router coroutines the platform portal calls:

  * `POST …/steps/{key}/form`               — the fact-finds (core, pension, employer)
  * `POST …/identity-documents/{item}/fulfill` — identity: the two documents, filed by the adviser
                                                (the check completes on its own)
  * `POST /aml/screen`                      — AML: Graphene screens the client from the console,
                                                and ComplyCube's clear result releases the step
  * `POST …/acknowledge`                    — the terms declarations, taken in person, with the
                                                SIGNED COPY the route requires
  * `POST …/action`                         — the platform hand-off

No client login and no fixture shortcut through the orchestrator. The one stand-in is ComplyCube
itself: its HTTP is a mock transport, and its clear result is routed the way the webhook consumer
routes it. If any step has no staff-reachable door, this test cannot reach the end.

Needs Docker (service_session fixture → PostgresContainer).
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from typing import Any

import httpx
import pytest
from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.auth import Principal
from app.complycube import routing
from app.complycube.client import ScreeningClient
from app.db import FirmContext
from app.flows import orchestrator
from app.flows.onboarding_seed import FLOW_CODE, seed_onboarding
from app.permissions import AML_WRITE, CASES_WRITE, CONTACTS_READ_ALL
from app.routers import aml as aml_router
from app.routers import applications as api
from tests.conftest import SECCL_PLATFORM_SQL

#: A minimal PDF: the store sniffs content, and both the signed terms and the identity documents
#: are refused unless they are a PDF or an image.
_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


@pytest.fixture(autouse=True)
def _no_object_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The walkthrough files real documents (signed terms, identity evidence) through the real
    upload path; only the bytes' destination is stubbed, so no bucket is needed."""

    async def _bucket(_session: AsyncSession, _tenant_id: int) -> str:
        return "documents-test"

    async def _put(
        _key: str, _data: bytes, _content_type: str, *, bucket: str | None = None
    ) -> None:
        pass

    monkeypatch.setattr(tenant_storage, "document_bucket", _bucket)
    monkeypatch.setattr(storage, "put_document", _put)


#: Answers for the seeded fact-finds, keyed by form code. Only the *required* questions matter —
#: `orchestrator._assert_fulfilment_supported` refuses a submission that misses one, which is what
#: makes this walkthrough meaningful rather than a series of status flips.
CORE_ANSWERS: dict[str, Any] = {
    "title": "Miss",
    "first_name": "Harriet",
    "surname": "Vale",
    "dob": "1971-03-14",
    "gender": "female",
    "nationality": ["GB"],
    "tax_residency": ["GB"],
    "national_identifier": "QQ123456C",
    "national_identifier_type": "National Insurance Number",
    "uk_national_insurance": "QQ123456C",
    "email": "harriet.vale@example.test",
    "phone": "+44 7700 900123",
    "other_numbers": [{"label": "home", "number": "0117 900 0000"}],
    "address": {
        "line1": "12 Beaufort Gardens",
        "city": "Bristol",
        "postcode": "BS8 2QT",
        "country": "GB",
    },
    "bank_account.account_name": "Miss H Vale",
    "bank_account.account_number": "12345678",
    "bank_account.sort_code": "12-34-56",
}


def _principal() -> Principal:
    return Principal(
        user_id="wos_adviser",
        organisation_id="org_1",
        role="adviser",
        permissions=[CASES_WRITE.slug, CONTACTS_READ_ALL.slug],
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _required_answers(sess: AsyncSession, form_uid: str) -> dict[str, Any]:
    """Every required question on a seeded form, answered. Falls back to a plausible value by type
    so adding a question to a seeded form doesn't silently make this walkthrough a no-op."""
    rows = (
        (
            await sess.execute(
                text(
                    "SELECT ff.field_key, ff.field_type, ff.required, ff.options, ff.validation "
                    "FROM documents.form_fields ff JOIN documents.forms f ON f.id = ff.form_id "
                    "WHERE f.uid = :u"
                ),
                {"u": form_uid},
            )
        )
        .mappings()
        .all()
    )
    answers: dict[str, Any] = {}
    for r in rows:
        key = str(r["field_key"])
        # An OPTIONAL field is skipped unless `CORE_ANSWERS` names it. The walkthrough otherwise
        # answers only what the form demands, which is the right default — but it means an
        # optional field can never be proved to reach its column. `other_numbers` is exactly that:
        # not required (most clients have one number), and the only end-to-end evidence that a
        # repeatable answer survives the mapper without being stringified element-wise.
        if key in CORE_ANSWERS:
            answers[key] = CORE_ANSWERS[key]
            continue
        if not r["required"]:
            continue
        if key == "account_name":
            answers[key] = "Test SIPP"
            continue
        if key == "currency":
            answers[key] = "GBP"
            continue
        if key == "investment_capacity":
            answers[key] = "advisory"
            continue
        ftype = str(r["field_type"])
        raw_options = r["options"] if isinstance(r["options"], dict) else {}
        opts = raw_options.get("options")
        source = raw_options.get("source")
        if opts:
            first = opts[0]["value"]
            answers[key] = [first] if ftype == "multiSelect" else first
        elif source:
            # A SOURCE-BACKED field has no static option list: the portals resolve it at render
            # time against live reference data (`{nationalities, countries, currencies, …}`), so
            # this has to answer with a value from that source or the walkthrough stops
            # exercising the real shape. It fell through to "Recorded by the adviser" the moment
            # `country_of_birth` moved off its hardcoded name list onto `ref.countries` — the
            # test still passed and had stopped proving the column accepts what the form posts.
            answers[key] = (
                [SOURCE_ANSWERS[source]] if ftype == "multiSelect" else SOURCE_ANSWERS[source]
            )
        elif ftype in {"number", "currency", "percentage"}:
            # A generic `1` is not a valid answer to every number: `pension.retirementAge` maps to a
            # column with a 50-75 CHECK, so the blanket value made the write-back fail on a
            # constraint rather than on anything the flow got wrong. Honour the field's own bounds.
            low = (
                ((r["validation"] or {}).get("min")) if isinstance(r["validation"], dict) else None
            )
            answers[key] = (
                int(low) if isinstance(low, (int, float, str)) and str(low).isdigit() else 1
            )
        elif ftype in {"boolean", "checkbox", "toggle"}:
            answers[key] = True
        elif ftype in {"date", "dateOfBirth"}:
            answers[key] = "1971-03-14"
        elif ftype == "address":
            answers[key] = {
                "line1": "1 Test Street",
                "city": "London",
                "postcode": "EC1A 1BB",
                "country": "GB",
            }
        elif ftype == "nationalIdentifier":
            answers[key] = "QQ123456C"
        elif ftype == "marital":
            answers[key] = "married"
        elif ftype == "employment":
            answers[key] = "employed"
        elif ftype == "gender":
            answers[key] = "female"
        elif ftype == "title":
            answers[key] = "Ms"
        else:
            minimum = (
                ((r["validation"] or {}).get("minLength"))
                if isinstance(r["validation"], dict)
                else None
            )
            answer = "Recorded by the adviser"
            if isinstance(minimum, int) and len(answer) < minimum:
                answer = "Recorded by the adviser with sufficient supporting detail"
            answers[key] = answer
    return answers


#: One representative value per option `source` the seeded flow uses. A KeyError here is the
#: point: a new source-backed field would otherwise be answered "Recorded by the adviser" and the
#: walkthrough would keep passing while testing nothing about it. Pinned from the other side by
#: `test_every_option_source_has_a_walkthrough_answer`.
SOURCE_ANSWERS: dict[str, Any] = {
    "nationalities": "GB",
    "countries": "GB",
}


def test_every_option_source_has_a_walkthrough_answer() -> None:
    """Every `{"source": …}` the seed declares can be answered by the generator above.

    The seed is where a field is added, and this file is where it gets exercised; without this
    the two drift silently in the direction that always looks green.
    """
    from app.flows import onboarding_seed

    declared = {
        options["source"]
        for options in _seed_option_objects(onboarding_seed)
        if isinstance(options, dict) and "source" in options
    }
    # `models`/`fee_packages`/`transfer_providers`/`currencies` are supplied per case by the
    # kickoff, not by reference data, and the walkthrough answers those steps from its own
    # fixtures — so only the reference sources have to be covered here.
    reference = {"nationalities", "countries"}
    assert reference <= declared, f"the seed no longer uses {sorted(reference - declared)}"
    assert reference <= set(SOURCE_ANSWERS)


def _seed_option_objects(module: Any) -> list[Any]:
    """Every field definition's options tuple-slot across the seed's field lists."""
    found: list[Any] = []
    for name in dir(module):
        if not name.startswith("_") and not name.isupper():
            continue
        value = getattr(module, name, None)
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, tuple) and len(row) >= 8:
                found.append(row[7])
    return found


class _Fx:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _seed_sipp_application(sess: AsyncSession) -> _Fx:
    """A tenant with the real seeded onboarding flow, and one SIPP account for one individual."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"ob_{s}",
            a=f"ob-{s}",
            n=f"OB {s}",
        )
    )
    seeded = await seed_onboarding(sess, tenant_id=tenant)
    assert seeded["created"] is True
    platform = str(
        await _scalar(
            sess,
            SECCL_PLATFORM_SQL,
            n=f"Seccl {s}",
        )
    )
    # A SIPP product, so the pension + employer-contribution branches of the seed light up — the
    # conditional steps the demo's "SIPP" choice is meant to produce.
    product = int(
        await _scalar(
            sess,
            "INSERT INTO platform.products (name, product_type) "
            "VALUES ('Seccl SIPP', CAST('SIPP' AS platform.product_type)) RETURNING id",
        )
    )
    app_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.applications (tenant_id, provider, uid, status, custody) "
            "VALUES (:t, :p, :u, 'draft', 'onshore') RETURNING id",
            t=tenant,
            p=platform,
            u=f"app_{s}",
        )
    )
    contact = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, surname, email) "
            "VALUES (:t, :u, 'individual', 'Harriet Vale', 'Harriet', 'Vale', :e) RETURNING id",
            t=tenant,
            u=f"con_{s}",
            e=f"harriet_{s}@example.test",
        )
    )
    account = int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts "
            "  (tenant_id, contact_id, application_id, product_id, status, custody, uid) "
            "VALUES (:t, :c, :a, :pr, 'draft', 'onshore', :u) RETURNING id",
            t=tenant,
            c=contact,
            a=app_id,
            pr=product,
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
    flow = (
        (
            await sess.execute(
                text(
                    "SELECT f.id, f.current_published_version_id AS vid, fv.definition "
                    "FROM flows.flows f JOIN flows.flow_versions fv "
                    "  ON fv.id = f.current_published_version_id "
                    "WHERE f.tenant_id = :t AND f.code = :c"
                ),
                {"t": tenant, "c": FLOW_CODE},
            )
        )
        .mappings()
        .one()
    )
    ctx = FirmContext(session=sess, tenant_id=tenant, principal=_principal())
    created = await orchestrator.start_execution(
        sess,
        tenant_id=tenant,
        flow_id=int(flow["id"]),
        flow_version_id=int(flow["vid"]),
        definition=flow["definition"],
        graph=await api._graph(sess, tenant, app_id),
        application_id=app_id,
        data={},
    )
    return _Fx(
        tenant=tenant,
        contact=contact,
        ctx=ctx,
        exec_uid=str(created["uid"]),
        execution_id=int(created["id"]),
    )


async def _open_steps(sess: AsyncSession, execution_id: int) -> list[dict[str, Any]]:
    """Every step still awaiting someone, with the case uid the router needs."""
    return [
        dict(r)
        for r in (
            await sess.execute(
                text(
                    "SELECT c.uid AS case_uid, c.kind, c.status AS case_status, cs.step_key, "
                    "       cs.step_type, cs.assignee, "
                    "       cs.status, cs.participant_subject_id, cs.request_item_id, "
                    "       cs.data -> 'awaiting' AS awaiting "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.status IN ('pending', 'waiting', 'blocked') "
                    "ORDER BY cs.id"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    ]


def _complycube(request: httpx.Request) -> httpx.Response:
    """ComplyCube's side of a console screen: no client yet, so one is created, then the check."""
    path = request.url.path.removeprefix("/v1")
    if request.method == "GET":
        return httpx.Response(200, json={"items": []})
    ids = {"/clients": "cli", "/addresses": "adr", "/checks": "chk"}
    return httpx.Response(200, json={"id": f"{ids.get(path, 'obj')}_{uuid.uuid4().hex[:10]}"})


async def _screen_clear(fx: _Fx, sess: AsyncSession, step: dict[str, Any]) -> None:
    """Graphene screens the client from the AML console, and ComplyCube answers clear."""
    contact_uid = await _scalar(
        sess, "SELECT uid FROM crm.contacts WHERE id = :c", c=step["participant_subject_id"]
    )
    operator = Principal(
        user_id="wos_operator", organisation_id="org_ops", permissions=[AML_WRITE.slug]
    )
    ctx = aml_router.AmlContext(
        session=sess,
        tenant_id=fx.tenant,
        targeted=True,
        foreign=False,
        principal=operator,
        user_id=str(uuid.uuid4()),
        own_tenant_id=fx.tenant,
    )

    def screening() -> ScreeningClient:
        return ScreeningClient("test-key", transport=httpx.MockTransport(_complycube))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(aml_router, "require_client", screening)
        started = await aml_router.run_screen(
            aml_router.ScreenRequest(contact_uid=str(contact_uid)), ctx=ctx
        )
    check_pk = await _scalar(
        sess, "SELECT id FROM compliance.checks WHERE uid = :u", u=started["check_uid"]
    )
    await sess.execute(
        text("UPDATE compliance.checks SET status = 'clear' WHERE id = :id"), {"id": check_pk}
    )
    record = await routing.load_check(sess, int(check_pk))
    assert record is not None
    assert await routing.route_result(sess, record, "pending", "clear") == ["portal"]


async def _act(fx: _Fx, sess: AsyncSession, step: dict[str, Any]) -> str:
    """Take the ONE staff action this step offers, or say there isn't one."""
    kind = str(step["step_type"])
    pid = step["participant_subject_id"]
    if kind == "fill_form":
        form_uid = await _scalar(
            sess,
            "SELECT form_uid FROM documents.request_items WHERE id = :i",
            i=step["request_item_id"],
        )
        await api.submit_step_form(
            fx.exec_uid,
            str(step["case_uid"]),
            str(step["step_key"]),
            api.StepFormSubmit(
                answers=await _required_answers(sess, str(form_uid)),
                participant_contact_id=pid,
            ),
            principal=_principal(),
            ctx=fx.ctx,
        )
        return "form"
    if kind == "accept_terms":
        # In person means the adviser uploads the copy the client signed; the route refuses the
        # acceptance without it.
        await api.acknowledge_terms(
            fx.exec_uid,
            case_uid=str(step["case_uid"]),
            step_key=str(step["step_key"]),
            participant_contact_id=pid,
            attested=True,
            note="Accepted with the client present",
            file=UploadFile(filename="terms-signed.pdf", file=BytesIO(_PDF)),
            principal=_principal(),
            ctx=fx.ctx,
        )
        return "acknowledge"
    if kind == "verify_identity":
        # Nobody passes an identity check by hand. The manual route IS its two documents: the
        # adviser files each one the client handed over, and the second landing completes the
        # check itself.
        recorded = await _scalar(
            sess,
            "SELECT cs.data -> 'identity_documents' FROM flows.case_steps cs "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.uid = :u AND cs.step_key = :k "
            "  AND (CAST(:p AS int) IS NULL OR cs.participant_subject_id = CAST(:p AS int))",
            u=step["case_uid"],
            k=step["step_key"],
            p=pid,
        )
        if isinstance(recorded, str):
            recorded = json.loads(recorded)
        assert isinstance(recorded, dict) and recorded, "the manual route raised its documents"
        for key, item_uid in recorded.items():
            await api.fulfil_identity_document(
                exec_uid=fx.exec_uid,
                item_uid=str(item_uid),
                case_uid=str(step["case_uid"]),
                step_key=str(step["step_key"]),
                participant_contact_id=pid,
                file=UploadFile(filename=f"{key}.pdf", file=BytesIO(_PDF)),
                principal=_principal(),
                ctx=fx.ctx,
            )
        return "identity-documents"
    if kind == "aml_check":
        # Nobody passes an AML screen by hand either: Graphene screens, and the result settles it.
        await _screen_clear(fx, sess, step)
        return "aml-screen"
    if kind in {"review", "task", "setup_account"}:
        await api.action_step(
            fx.exec_uid,
            api.ActionBody(
                case_uid=str(step["case_uid"]),
                step_key=str(step["step_key"]),
                participant_contact_id=pid,
                outcome="passed",
                note="Checked by the adviser",
                adviser_confirmed=step["step_key"] == "declaration",
            ),
            principal=_principal(),
            ctx=fx.ctx,
        )
        return "action"
    raise AssertionError(f"no staff action exists for a {kind!r} step ({step['step_key']})")


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="setup_account is async on baton; this walkthrough drives the journey in one "
    "uncommitted transaction the worker cannot see. Needs a baton in-session advance for "
    "the setup door — see docs/baton.md."
)
async def test_an_adviser_can_finish_the_seeded_onboarding_alone(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_sipp_application(sess)

    # The SIPP branches planned: this is the demo's "select the account type" having an effect.
    keys = {s["step_key"] for s in await _open_steps(sess, fx.execution_id)}
    assert {"core", "pension", "employer"} <= keys

    # Nothing in flight is assigned to the client except the terms declarations and the signed
    # W-9 / W-8, which is asked for only when the declaration's fork finds one recorded.
    client_types = {
        s["step_type"]
        for s in await _open_steps(sess, fx.execution_id)
        if s["assignee"] == "client"
    }
    assert client_types <= {"accept_terms", "upload_document"}

    taken: list[str] = []
    # Bounded: each pass must settle at least one step or the graph is stuck. 40 is far above the
    # seeded flow's step count, so exhausting it is a failure, not a slow success.
    for _ in range(40):
        steps = await _open_steps(sess, fx.execution_id)
        if not steps:
            break
        actionable = [
            s
            for s in steps
            # Exactly what the portal offers a control for (`journeys/steps.ts::stepAction`): an
            # open step, not held on a prerequisite, whose artifact exists. `blocked` is NOT
            # actionable — it is how the account tier waits for every owner's KYC, and an
            # adviser clicking through it would open the account before identity and AML cleared.
            if s["status"] in {"pending", "waiting"}
            and not (s["awaiting"] or [])
            and not (s["step_type"] in {"fill_form", "accept_terms"} and not s["request_item_id"])
            # The account tier's entry gate: `setup_account` is not offered while the account case
            # is blocked on its owners' KYC, and the API refuses it too.
            and not (s["step_type"] == "setup_account" and s["case_status"] == "blocked")
        ]
        assert actionable, f"every remaining step is blocked: {[s['step_key'] for s in steps]}"
        before = len(steps)
        for step in actionable:
            taken.append(f"{step['step_key']}:{await _act(fx, sess, step)}")
        assert len(await _open_steps(sess, fx.execution_id)) < before, (
            f"a pass settled nothing — stuck at {[s['step_key'] for s in steps]}"
        )
    else:  # pragma: no cover - only on a genuine loop
        raise AssertionError(f"the journey never settled; actions taken: {taken}")

    status = await _scalar(
        sess, "SELECT status FROM flows.executions WHERE id = :e", e=fx.execution_id
    )
    assert status == "completed", f"journey ended {status}; actions taken: {taken}"
    # Additional information recorded no W-9 or W-8, so the client was never asked for one.
    assert (
        await _scalar(
            sess,
            "SELECT cs.status FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'fatca_form'",
            e=fx.execution_id,
        )
        == "skipped"
    )
    assert (
        await _scalar(
            sess,
            "SELECT a.adviser_declaration FROM crm.applications a "
            "JOIN flows.executions e ON e.crm_application_id = a.id WHERE e.id = :e",
            e=fx.execution_id,
        )
        is True
    )
    # The hand-off ran: application stamped synced AND the draft account is now active in
    # Graphene's book (local activation — no custodian call), with the ops-visible
    # custodian_open.pending_ops marker on accounts.data.
    assert (
        await _scalar(
            sess,
            "SELECT a.synced FROM crm.applications a JOIN flows.executions e "
            "  ON e.crm_application_id = a.id WHERE e.id = :e",
            e=fx.execution_id,
        )
        is True
    )
    assert (
        await _scalar(
            sess,
            "SELECT acct.status::text FROM platform.accounts acct "
            "JOIN flows.cases c ON c.subject_id = acct.id AND c.subject_type = 'account' "
            "JOIN flows.executions e ON e.id = c.execution_id "
            "WHERE e.id = :e LIMIT 1",
            e=fx.execution_id,
        )
        == "active"
    )
    assert (
        await _scalar(
            sess,
            "SELECT acct.data -> 'custodian_open' ->> 'status' FROM platform.accounts acct "
            "JOIN flows.cases c ON c.subject_id = acct.id AND c.subject_type = 'account' "
            "JOIN flows.executions e ON e.id = c.execution_id "
            "WHERE e.id = :e LIMIT 1",
            e=fx.execution_id,
        )
        == "pending_ops"
    )
    # And the fact-find wrote back to the client's profile, which is the point of `mode: gather`.
    assert (
        await _scalar(sess, "SELECT surname FROM crm.contacts WHERE id = :c", c=fx.contact)
        == "Vale"
    )
    # BOTH HALVES OF THE PHONE ANSWER, end to end. The fact-find's one number is the PRIMARY
    # NUMBER (source key `phone`, kept so an in-flight case's stored answer still resolves) and
    # the `other_numbers` question lands in the jsonb array beside it as the SECONDARY number.
    #
    # This is the claim worth pinning, because the mapper is `str -> str` by design: a list of
    # OBJECTS used to be stringified element-wise, so the column would have read
    # `["{'label': 'home', 'number': '0117 900 0000'}"]` — stored without complaint and readable
    # by nothing. It is also the proof the numbers are normalised on the way in by the same door
    # the client record uses: the answer is typed with spaces and lands compacted.
    numbers = (
        (
            await sess.execute(
                text("SELECT mobile, phone FROM crm.contacts WHERE id = :c"),
                {"c": fx.contact},
            )
        )
        .mappings()
        .one()
    )
    assert numbers["mobile"] == "+447700900123"
    assert numbers["phone"] == "+441179000000"
    # THE KYC ANSWERS LAND IN THEIR OWN COLUMNS (0116), not in `data`. Before that migration the
    # offshore fact-find's answers folded into the jsonb and reached a screen only as a dotted
    # path; the seed's own comment said so.
    kyc = (
        (
            await sess.execute(
                text(
                    "SELECT country_of_birth, occupation, source_of_funds, town_of_birth "
                    "FROM crm.contacts WHERE id = :c"
                ),
                {"c": fx.contact},
            )
        )
        .mappings()
        .one()
    )
    # A CODE, joinable to `ref.countries` — not the display name. This is the end-to-end claim:
    # the question now reads its options from `ref.countries` with `valueKey: code`, so the value
    # that lands is the same shape `nationality` already stores. It held "United Kingdom" while
    # `nationality` held `{GB}` — one fact, two spellings, in one row.
    assert kyc["country_of_birth"] == "GB"
    resolved = await _scalar(
        sess, "SELECT name FROM ref.countries WHERE code = :c", c=kyc["country_of_birth"]
    )
    assert resolved == "United Kingdom", "the stored code must resolve to a country"
    # The rest of the block reached its columns too.
    assert kyc["occupation"]
    assert kyc["source_of_funds"]
    # UAT-9: agreeing the required terms writes ``crm.kyc_checks.kind = 'terms'``. Without this
    # the Compliance card stayed "Not recorded" after every completed SIPP walk.
    terms_status = await _scalar(
        sess,
        "SELECT status FROM crm.kyc_checks WHERE contact_id = :c AND kind = 'terms' "
        "ORDER BY id DESC LIMIT 1",
        c=fx.contact,
    )
    assert terms_status == "passed"


@pytest.mark.asyncio
async def test_republishing_the_seed_versions_it_instead_of_rewriting_history(
    service_session: AsyncSession,
) -> None:
    """`seed_onboarding` is re-run on every tenant touch. If it edited the live version in place, a
    journey in flight would silently change shape underneath the adviser; if it were a no-op, a
    tenant provisioned before a seed fix could never reach the new graph."""
    sess = service_session
    fx = await _seed_sipp_application(sess)
    live_before = await _scalar(
        sess,
        "SELECT current_published_version_id FROM flows.flows WHERE tenant_id = :t AND code = :c",
        t=fx.tenant,
        c=FLOW_CODE,
    )
    again = await seed_onboarding(sess, tenant_id=fx.tenant)
    assert again["created"] is False
    assert again["updated"] is False  # nothing changed, so no churn
    assert (
        await _scalar(
            sess,
            "SELECT current_published_version_id FROM flows.flows "
            "WHERE tenant_id = :t AND code = :c",
            t=fx.tenant,
            c=FLOW_CODE,
        )
        == live_before
    )
    # Now move the definition on. A NEW version is published; the running execution keeps its own.
    await sess.execute(
        text(
            "UPDATE flows.flow_versions SET definition = definition || CAST(:d AS jsonb) "
            "WHERE id = :v"
        ),
        {"d": '{"milestones": [{"id": "stale", "title": "Stale"}]}', "v": live_before},
    )
    third = await seed_onboarding(sess, tenant_id=fx.tenant)
    assert third["updated"] is True
    live_after = await _scalar(
        sess,
        "SELECT current_published_version_id FROM flows.flows WHERE tenant_id = :t AND code = :c",
        t=fx.tenant,
        c=FLOW_CODE,
    )
    assert live_after != live_before
    assert (
        await _scalar(
            sess, "SELECT flow_version_id FROM flows.executions WHERE id = :e", e=fx.execution_id
        )
        == live_before
    )


@pytest.mark.asyncio
async def test_republish_skips_over_an_existing_draft_version_number(
    service_session: AsyncSession,
) -> None:
    """A draft at live+1 must not make the seed collide on the version uniqueness constraint.

    The builder leaves drafts like that; ``_republish`` must take MAX(version)+1.
    """
    sess = service_session
    fx = await _seed_sipp_application(sess)
    flow_id = int(
        await _scalar(
            sess,
            "SELECT id FROM flows.flows WHERE tenant_id = :t AND code = :c",
            t=fx.tenant,
            c=FLOW_CODE,
        )
    )
    live_ver = int(
        await _scalar(
            sess,
            "SELECT fv.version FROM flows.flows f "
            "JOIN flows.flow_versions fv ON fv.id = f.current_published_version_id "
            "WHERE f.id = :f",
            f=flow_id,
        )
    )
    # Occupy the naive next slot with a draft the seed did not create.
    await sess.execute(
        text(
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition) "
            "VALUES (:u, :t, :f, :v, 'draft', CAST('{\"version\": 3, \"nodes\": []}' AS jsonb))"
        ),
        {
            "u": f"flv_draft_{fx.tenant}",
            "t": fx.tenant,
            "f": flow_id,
            "v": live_ver + 1,
        },
    )
    # Force a content change so republish actually writes.
    live_id = int(
        await _scalar(
            sess,
            "SELECT current_published_version_id FROM flows.flows WHERE id = :f",
            f=flow_id,
        )
    )
    await sess.execute(
        text(
            "UPDATE flows.flow_versions SET definition = definition || CAST(:d AS jsonb) "
            "WHERE id = :v"
        ),
        {"d": '{"milestones": [{"id": "bump", "title": "Bump"}]}', "v": live_id},
    )
    out = await seed_onboarding(sess, tenant_id=fx.tenant)
    assert out["updated"] is True
    new_ver = int(
        await _scalar(
            sess,
            "SELECT fv.version FROM flows.flows f "
            "JOIN flows.flow_versions fv ON fv.id = f.current_published_version_id "
            "WHERE f.id = :f",
            f=flow_id,
        )
    )
    assert new_ver == live_ver + 2  # skipped the occupied draft slot


@pytest.mark.asyncio
async def test_initial_details_release_all_relevant_sipp_fact_find_forms_together(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_sipp_application(sess)
    before = await _open_steps(sess, fx.execution_id)
    core = next(step for step in before if step["step_key"] == "core")
    assert core["request_item_id"] is not None
    assert all(
        step["request_item_id"] is None
        for step in before
        if step["step_key"] in {"additional_info", "pension", "employer", "account_preferences"}
    )

    await _act(fx, sess, core)

    after = {
        step["step_key"]: step
        for step in await _open_steps(sess, fx.execution_id)
        if step["step_key"] in {"additional_info", "pension", "employer", "account_preferences"}
    }
    assert set(after) == {"additional_info", "pension", "employer", "account_preferences"}
    assert all(step["request_item_id"] is not None for step in after.values())
    assert all(not (step["awaiting"] or []) for step in after.values())


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="setup_account is async on baton; this walkthrough drives the journey in one "
    "uncommitted transaction the worker cannot see. Needs a baton in-session advance for "
    "the setup door — see docs/baton.md."
)
async def test_the_account_cannot_be_opened_before_the_owners_kyc_clears(
    service_session: AsyncSession,
) -> None:
    """The account tier's entry gate, asserted at the transition and not only in the projection.

    ``setup_account`` sits ``pending`` while its account case is ``blocked`` (the case status
    carries the gate, the step status does not), so before this guard a ``cases:write`` holder could
    record the platform hand-off — the step that opens the account with the custodian — with no
    identity or AML recorded at all. The portal hides the control; this makes the refusal real.
    """
    sess = service_session
    fx = await _seed_sipp_application(sess)
    setup = (
        (
            await sess.execute(
                text(
                    "SELECT c.uid AS case_uid, cs.status FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.step_key = 'setup'"
                ),
                {"e": fx.execution_id},
            )
        )
        .mappings()
        .one()
    )
    # Not blocked at the STEP level — which is exactly why the case-level gate had to be asserted.
    assert setup["status"] == "pending"
    body = api.ActionBody(case_uid=str(setup["case_uid"]), step_key="setup", outcome="passed")
    with pytest.raises(Exception) as exc:
        await api.action_step(fx.exec_uid, body, principal=_principal(), ctx=fx.ctx)
    assert "onboarding steps are complete" in str(exc.value)
    assert (
        await _scalar(
            sess,
            "SELECT cs.status FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'setup'",
            e=fx.execution_id,
        )
        != "fulfilled"
    )
    # The provider write-back has NOT run.
    assert (
        await _scalar(
            sess,
            "SELECT a.synced FROM crm.applications a JOIN flows.executions e "
            "  ON e.crm_application_id = a.id WHERE e.id = :e",
            e=fx.execution_id,
        )
        is not True
    )

    # Clear everything ahead of the account opening — the fact-finds, the client's terms, and then
    # identity and AML, which is exactly what `_recompute` requires of `gate_open`.
    #
    # Deliberately NOT "the profile case only", which is what this drove before the spine was put
    # back into the legacy order. Identity and AML are the owner's (participant tier) but now run
    # AFTER the terms they signed (account tier), so a loop that only ever touched the profile case
    # could never release them and the owner's KYC would never clear. Ordering crosses tiers now
    # (`dependencies.unmet`'s `foreign`), and this is what that looks like from the outside.
    for _ in range(12):
        ready = [
            s
            for s in await _open_steps(sess, fx.execution_id)
            if s["step_key"] != "setup"
            and s["status"] in {"pending", "waiting"}
            and not (s["awaiting"] or [])
        ]
        if not ready:
            break
        for step in ready:
            await _act(fx, sess, step)
    await api.action_step(fx.exec_uid, body, principal=_principal(), ctx=fx.ctx)
    assert (
        await _scalar(
            sess,
            "SELECT cs.status FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'setup'",
            e=fx.execution_id,
        )
        == "fulfilled"
    )
