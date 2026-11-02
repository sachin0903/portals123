"""The identity check is automatic or manual by the CLIENT's choice.

They hold the document and know whether they can photograph it, so it is not a firm setting and
not the adviser's guess. The provider the definition names is only the default: the automatic
check is ComplyCube's, which asks the client for three photos (``app/idv/capture.py``), and the
manual one is two documents a person reviews (``app/idv/manual.py``).

Two halves are tested here: the handler reads a recorded choice in preference to the pinned
provider (a unit test, no DB), and the client's own endpoint records it, asks for the two
documents, and moves the step to staff (against real Postgres). The adviser can make the same
write from the journey when the documents are already in front of them. The photos the digital
check asked for are slots on the step, never items on the client's checklist: the choice leaves
the slots unused and deletes any photo held for them (``app/idv/held.py``).
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.auth import Principal
from app.config import settings
from app.db import FirmContext
from app.flows import (
    orchestrator,
    step_handlers,  # noqa: F401 - registers the handlers on import
)
from app.flows.errors import StepTransitionRefused
from app.flows.handlers import StepContext, get_handler
from app.idv import capture as idv_capture
from app.idv import held as idv_held
from app.idv import manual as idv_manual
from app.permissions import CONTACTS_READ_ALL
from app.routers import applications as api
from app.routers.applications import IdentityChoice
from tests.conftest import SECCL_PLATFORM_SQL


def _ctx(**kw: Any) -> StepContext:
    return StepContext(
        session=cast(AsyncSession, None),
        tenant_id=1,
        execution_id=1,
        case_id=1,
        step_key="idv",
        config=cast(dict, kw.get("config", {})),
        participant_subject_id=7,
        data=cast(dict, kw.get("data", {})),
    )


def test_the_pinned_provider_is_only_the_default() -> None:
    handler = get_handler("verify_identity")
    assert handler._provider(_ctx(config={"provider": "complycube"})) == "complycube"
    assert handler._provider(_ctx(config={})) == "manual"


def test_a_retired_idenfy_pin_still_reads_as_the_default() -> None:
    """A published version is immutable, so a journey in flight can still name ``idenfy``. It is
    the default like any other pin, the check it opens is recorded as ComplyCube (which runs it),
    and the client's choice still beats it."""
    handler = get_handler("verify_identity")
    pinned = _ctx(config={"provider": "idenfy"})
    assert handler._provider(pinned) == "idenfy"
    assert handler._provider_label(pinned) == "complycube"
    chosen = _ctx(config={"provider": "idenfy"}, data={"identity_choice": "manual"})
    assert handler._provider(chosen) == "manual"
    assert handler._provider_label(chosen) == "manual"


def test_a_recorded_choice_beats_the_pinned_provider() -> None:
    """The client asked for a person, so nothing may ask them for photos again — which is what a
    retry would otherwise do, against a check an adviser may already be reviewing."""
    handler = get_handler("verify_identity")
    ctx = _ctx(config={"provider": "complycube"}, data={"identity_choice": "manual"})
    assert handler._provider(ctx) == "manual"


def test_the_choice_only_moves_one_way() -> None:
    """There is no ``identity_choice: 'complycube'`` — choosing the digital path is simply doing
    it, and an unrecognised value must not silently re-enable a provider the client declined."""
    handler = get_handler("verify_identity")
    assert handler._provider(_ctx(config={}, data={"identity_choice": "complycube"})) == "manual"
    assert (
        handler._provider(
            _ctx(config={"provider": "complycube"}, data={"identity_choice": "nonsense"})
        )
        == "complycube"
    )


# ── The client's own endpoint ─────────────────────────────────────────────────────────────────────


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _seed_client_identity_step(sess: AsyncSession, *, capture: bool = True) -> dict[str, Any]:
    """A tenant, a client contact with a portal login, and their own open `verify_identity` step,
    shaped the way a ComplyCube step is: a pending check recorded as ``complycube`` and, unless
    ``capture`` is false, the three photo slots it asks the client to fill.

    Built directly rather than by running a journey: what is under test is the choice, and a real
    onboarding run would need a ComplyCube key to reach the same state.
    """
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"id_{s}",
            a=f"id-{s}",
            n=f"Identity {s}",
        )
    )
    platform = str(await _scalar(sess, SECCL_PLATFORM_SQL, n=f"Seccl {s}"))
    contact = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, surname, email) "
            "VALUES (:t, :u, 'individual', 'Ivy Doyle', 'Ivy', 'Doyle', :e) RETURNING id",
            t=tenant,
            u=uuid.uuid4().hex[:12],
            e=f"ivy_{s}@t.test",
        )
    )
    user = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
            "VALUES (:uid, :t, :e, :w, true) RETURNING id",
            uid=f"iusr_{s}",
            t=tenant,
            e=f"ivy_{s}@t.test",
            w=f"iwos_{s}",
        )
    )
    await sess.execute(
        text("INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) VALUES (:t, :c, :u)"),
        {"t": tenant, "c": contact, "u": user},
    )
    application = int(
        await _scalar(
            sess,
            "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
            "VALUES (:t, CAST(:p AS platform.provider_code), :u, 'identity_under_review') "
            "RETURNING id",
            t=tenant,
            p=platform,
            u=uuid.uuid4().hex[:12],
        )
    )
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, 'onboarding', 'Onboarding', 'published') RETURNING id",
            u=f"iflw_{s}",
            t=tenant,
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"iflv_{s}",
            t=tenant,
            f=flow_id,
            d=json.dumps({"steps": [], "milestones": []}),
        )
    )
    execution_uid = uuid.uuid4().hex[:12]
    execution = int(
        await _scalar(
            sess,
            "INSERT INTO flows.executions "
            "  (uid, tenant_id, flow_id, flow_version_id, crm_application_id, status) "
            "VALUES (:u, :t, :f, :v, :a, 'active') RETURNING id",
            u=execution_uid,
            t=tenant,
            f=flow_id,
            v=version_id,
            a=application,
        )
    )
    case_uid = uuid.uuid4().hex[:12]
    case = int(
        await _scalar(
            sess,
            "INSERT INTO flows.cases "
            "  (uid, tenant_id, execution_id, flow_id, flow_version_id, kind, subject_type, "
            "   subject_id, status) "
            "VALUES (:u, :t, :e, :f, :v, 'profile', 'contact', :c, 'active') RETURNING id",
            u=case_uid,
            t=tenant,
            e=execution,
            f=flow_id,
            v=version_id,
            c=contact,
        )
    )
    check = int(
        await _scalar(
            sess,
            "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status, provider) "
            "VALUES (:u, :t, :c, 'identity', 'pending', 'complycube') RETURNING id",
            u=uuid.uuid4().hex[:12],
            t=tenant,
            c=contact,
        )
    )
    await sess.execute(
        text(
            "INSERT INTO flows.case_steps "
            # `step_id` and `step_key` are both real columns (0025 added the version-stable key);
            # the journey surfaces read `step_key`, so a row with only `step_id` is invisible.
            "  (tenant_id, case_id, step_id, step_key, step_type, title, assignee, status, "
            "   participant_subject_id, data) "
            "VALUES (:t, :c, 'idv', 'idv', 'verify_identity', 'Verify identity', 'client', "
            "        'pending', :psid, CAST(:d AS jsonb))"
        ),
        {
            "t": tenant,
            "c": case,
            "psid": contact,
            "d": json.dumps({"kyc_check_id": check}),
        },
    )
    fx: dict[str, Any] = {
        "tenant": tenant,
        "contact": contact,
        "workos": f"iwos_{s}",
        "case": case,
        "case_uid": case_uid,
        "execution": execution,
        "execution_uid": execution_uid,
        "check": check,
        "capture": None,
    }
    if capture:
        fx["capture"] = await _raise_capture(sess, fx)
    return fx


async def _raise_capture(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    """What ``ComplyCubeIdentityProvider.start`` does for the step: ask for the three photos."""
    return await idv_capture.raise_request(
        sess, contact_id=fx["contact"], case_id=fx["case"], step_key="idv"
    )


async def _request_items(sess: AsyncSession, fx: dict[str, Any]) -> list[str]:
    """The titles of the items on the client's checklist, oldest first."""
    rows = (
        await sess.execute(
            text("SELECT title FROM documents.request_items WHERE tenant_id = :t ORDER BY id"),
            {"t": fx["tenant"]},
        )
    ).all()
    return [str(r.title) for r in rows]


def _client_principal(fx: dict[str, Any]) -> Principal:
    return Principal(
        user_id=fx["workos"],
        organisation_id="org_1",
        role="client",
        permissions=[],
    )


def _staff_principal(fx: dict[str, Any]) -> Principal:
    return Principal(
        user_id="staff_wos",
        organisation_id="org_1",
        role="adviser",
        permissions=["cases:write", CONTACTS_READ_ALL.slug],
    )


@pytest.mark.asyncio
async def test_choosing_manual_records_it_and_moves_the_step_to_staff(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    # contact_scope() is claim-driven, so publish the claims the request layer would have set.
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {
            "c": json.dumps(
                {
                    "sub": fx["workos"],
                    "tenant_id": fx["tenant"],
                    "contact_ids": [fx["contact"]],
                    "portal": "client",
                    "role": "client",
                    "permissions": [],
                }
            )
        },
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    out = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"),
        principal=_client_principal(fx),
        ctx=ctx,
    )
    assert out["provider"] == "manual"

    row = (
        (
            await sess.execute(
                text(
                    "SELECT assignee, data ->> 'identity_choice' AS choice "
                    "FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    # The step is the adviser's: left with the client it would sit in a queue nobody can clear.
    assert row["assignee"] == "staff"
    assert row["choice"] == "manual"
    # The check keeps its identity and history; only who is doing it changes.
    assert (
        await _scalar(
            sess,
            "SELECT provider FROM crm.kyc_checks WHERE id = :i",
            i=fx["check"],
        )
        == "manual"
    )
    assert (
        await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :i", i=fx["check"])
        == "pending"
    )


@pytest.mark.asyncio
async def test_an_adviser_can_take_an_open_identity_check_onto_documents(
    service_session: AsyncSession,
) -> None:
    """The digital check is the client's photos. When the documents are already with the adviser,
    the journey lets them file those instead of waiting."""
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=True)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_staff_principal(fx))

    out = await api.take_identity_manual(
        fx["execution_uid"],
        api.TakeIdentityManual(
            case_uid=fx["case_uid"],
            step_key="idv",
            participant_contact_id=fx["contact"],
        ),
        principal=_staff_principal(fx),
        ctx=ctx,
    )
    assert out["provider"] == "manual"
    assert [item["key"] for item in out["documents"]] == ["photo_id", "proof_of_address"]
    row = (
        (
            await sess.execute(
                text(
                    "SELECT assignee, data ->> 'identity_choice' AS choice "
                    "FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["assignee"] == "staff"
    assert row["choice"] == "manual"
    assert (
        await _scalar(sess, "SELECT provider FROM crm.kyc_checks WHERE id = :i", i=fx["check"])
        == "manual"
    )


@pytest.mark.asyncio
async def test_an_adviser_cannot_take_a_check_graphene_is_reviewing(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=True)
    await sess.execute(
        text(
            "UPDATE flows.case_steps SET data = data || "
            '\'{"identity_capture": {"status": "review"}}\'::jsonb '
            "WHERE case_id = :c AND step_key = 'idv'"
        ),
        {"c": fx["case"]},
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_staff_principal(fx))
    with pytest.raises(HTTPException) as caught:
        await api.take_identity_manual(
            fx["execution_uid"],
            api.TakeIdentityManual(
                case_uid=fx["case_uid"],
                step_key="idv",
                participant_contact_id=fx["contact"],
            ),
            principal=_staff_principal(fx),
            ctx=ctx,
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_choosing_manual_twice_is_refused(service_session: AsyncSession) -> None:
    """The first choice may already have moved the step into someone's queue, so a second write is
    a 409 rather than a silent no-op."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {
            "c": json.dumps(
                {
                    "sub": fx["workos"],
                    "tenant_id": fx["tenant"],
                    "contact_ids": [fx["contact"]],
                    "portal": "client",
                    "role": "client",
                    "permissions": [],
                }
            )
        },
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    with pytest.raises(HTTPException) as exc:
        await api.my_identity_manual(
            body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
        )
    # 409, not 404: the step still exists and is still theirs, although the first call moved it to
    # staff. A 404 would tell a client who double-submitted that they have nothing to do.
    assert exc.value.status_code == 409
    assert "already being handled" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_client_with_nothing_to_verify_gets_a_404(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {
            "c": json.dumps(
                {
                    "sub": fx["workos"],
                    "tenant_id": fx["tenant"],
                    "contact_ids": [fx["contact"]],
                    "portal": "client",
                    "role": "client",
                    "permissions": [],
                }
            )
        },
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    with pytest.raises(HTTPException) as exc:
        await api.my_identity_manual(
            body=IdentityChoice(step_key="not_a_step"),
            principal=_client_principal(fx),
            ctx=ctx,
        )
    assert exc.value.status_code == 404


async def _claims(sess: AsyncSession, fx: dict[str, Any]) -> None:
    """Publish the claims the request layer would have set — `contact_scope()` is claim-driven."""
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {
            "c": json.dumps(
                {
                    "sub": fx["workos"],
                    "tenant_id": fx["tenant"],
                    "contact_ids": [fx["contact"]],
                    "portal": "client",
                    "role": "client",
                    "permissions": [],
                }
            )
        },
    )


@pytest.mark.asyncio
async def test_choosing_manual_asks_for_photo_id_and_proof_of_address(
    service_session: AsyncSession,
) -> None:
    """A manual check IS those two documents.

    They are raised as ordinary upload items on the client's own request, so the client's existing
    upload surface fulfils them and the files land as identity documents on their record — on the
    record, attributed, and with the journey showing whether each has arrived. Nothing else is on
    it: the photos the digital check asked for were never checklist items.
    """
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    out = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    documents = out["documents"]
    assert isinstance(documents, list)
    # Photo ID first: it is the harder one to find, and a proof of address is worthless without it.
    assert [d["key"] for d in documents] == ["photo_id", "proof_of_address"]
    assert [d["title"] for d in documents] == [spec.title for spec in idv_manual.DOCUMENTS]
    assert all(d["status"] == "pending" for d in documents)
    assert all(d["request_uid"] for d in documents)

    rows = (
        (
            await sess.execute(
                text(
                    "SELECT ri.action, ri.category, ri.title, r.contact_id "
                    "FROM documents.request_items ri "
                    "JOIN documents.requests r ON r.id = ri.request_id "
                    "WHERE ri.tenant_id = :t AND ri.status = 'pending' ORDER BY ri.id"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert [r["title"] for r in rows] == [spec.title for spec in idv_manual.DOCUMENTS]
    assert [r["action"] for r in rows] == ["upload", "upload"]
    # `identity`, so the fulfilment path files each upload as identity evidence on their record
    # rather than as "other".
    assert {r["category"] for r in rows} == {"identity"}
    assert {r["contact_id"] for r in rows} == {fx["contact"]}


@pytest.mark.asyncio
async def test_choosing_manual_leaves_the_photo_slots_unused(
    service_session: AsyncSession,
) -> None:
    """The photos the digital check asked for are slots on the step, not checklist items, so
    nothing of them sits beside the two documents that replace them, and there is nothing to
    close. The slots stay on the step, unused."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    photos = idv_capture.item_uids(fx["capture"])
    assert set(photos) == set(idv_capture.KEYS)
    assert await _request_items(sess, fx) == []
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )

    # The checklist holds the two documents and nothing else.
    assert await _request_items(sess, fx) == [spec.title for spec in idv_manual.DOCUMENTS]
    stored = await _scalar(
        sess,
        "SELECT data -> 'identity_capture' FROM flows.case_steps "
        "WHERE case_id = :c AND step_key = 'idv'",
        c=fx["case"],
    )
    state = idv_capture.state_of({idv_capture.CAPTURE_KEY: stored})
    assert state is not None
    assert idv_capture.item_uids(state) == photos
    assert state["status"] == idv_capture.CAPTURING
    assert idv_capture.PROGRESS_KEY not in state
    # And the client's own photo view answers that their identity is not checked from photos.
    with pytest.raises(HTTPException) as exc:
        await api.my_identity_capture(step_key="idv", principal=_client_principal(fx), ctx=ctx)
    assert exc.value.status_code == 404


async def _held_photos(sess: AsyncSession, fx: dict[str, Any]) -> list[str]:
    """The item keys of the photos held for the client's identity step."""
    rows = (
        await sess.execute(
            text("SELECT item_key FROM compliance.identity_photos WHERE case_id = :c ORDER BY id"),
            {"c": fx["case"]},
        )
    ).all()
    return [str(r.item_key) for r in rows]


async def _hold_photos(sess: AsyncSession, fx: dict[str, Any]) -> None:
    """The client sent their front and selfie: both held sealed for the step, as the submit does."""
    photo = idv_held.Photo(data=b"\xff\xd8\xff\xe0" + b"\x00" * 40_000, content_type="image/jpeg")
    await idv_held.hold(
        sess,
        idv_held.StepRef(
            tenant_id=fx["tenant"], case_id=fx["case"], step_key="idv", contact_id=fx["contact"]
        ),
        idv_capture.item_uids(fx["capture"]),
        {idv_capture.FRONT: photo, idv_capture.SELFIE: photo},
    )


@pytest.mark.asyncio
async def test_choosing_manual_deletes_the_photos_held_for_the_digital_check(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person checks the documents now, so nothing needs the photos the client sent for the
    digital check: they go with the choice. Another client's held photos are left alone."""
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    other = await _seed_client_identity_step(sess)
    await _hold_photos(sess, fx)
    await _hold_photos(sess, other)
    assert await _held_photos(sess, fx) == [idv_capture.FRONT, idv_capture.SELFIE]
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )

    assert await _held_photos(sess, fx) == []
    assert await _held_photos(sess, other) == [idv_capture.FRONT, idv_capture.SELFIE]


@pytest.mark.asyncio
async def test_the_two_documents_are_evidence_not_the_decision(
    service_session: AsyncSession,
) -> None:
    """Neither item is linked to the step's own `request_item_id`.

    That link is what the document-fulfilment hook routes back through, so linking one would let a
    photograph landing COMPLETE the identity check — the exact thing choosing a manual review asks
    a person to do.
    """
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    assert (
        await _scalar(
            sess,
            "SELECT request_item_id FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'",
            c=fx["case"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_the_documents_survive_a_reload(service_session: AsyncSession) -> None:
    """The client closes the dialog and comes back: the same two items, with their live status.

    The read is deliberately separate from the choice that raised them — the only other record of
    what was asked for was a response nobody kept.
    """
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    before = await api.my_identity_documents(
        step_key="idv", principal=_client_principal(fx), ctx=ctx
    )
    # Nothing chosen yet, so nothing has been asked for.
    assert before == {"chosen": False, "documents": []}

    raised = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    items = raised["documents"]
    assert isinstance(items, list)

    # The reload returns the SAME two items with their live status, although the choice
    # reassigned the step to staff: the client is still the one with something to upload.
    after = await api.my_identity_documents(
        step_key="idv", principal=_client_principal(fx), ctx=ctx
    )
    assert after["chosen"] is True
    assert isinstance(after["documents"], list)
    assert [d["item_uid"] for d in after["documents"]] == [d["item_uid"] for d in items]
    assert [d["key"] for d in after["documents"]] == ["photo_id", "proof_of_address"]

    # Reading the step directly (as staff would) proves the uids were recorded on it.
    # Keyed BY SPEC, not a bare list: pairing a title to a uid by position mislabels them the
    # moment the list is not exactly the two, in order.
    recorded = await _scalar(
        sess,
        "SELECT data -> 'identity_documents' FROM flows.case_steps "
        "WHERE case_id = :c AND step_key = 'idv'",
        c=fx["case"],
    )
    assert recorded == {d["key"]: d["item_uid"] for d in items}


@pytest.mark.asyncio
async def test_one_document_of_two_does_not_complete_the_check(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check needs BOTH pieces. One landing files evidence and leaves the step waiting —
    "we have one of the two" is the state a chase is about, not a verified identity."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    client_ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    raised = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"),
        principal=_client_principal(fx),
        ctx=client_ctx,
    )
    items = raised["documents"]
    assert isinstance(items, list)
    item = items[0]

    async def _bucket(_session: AsyncSession, _tenant_id: int) -> str:
        return "documents-test"

    async def _put(
        _key: str, _data: bytes, _content_type: str, *, bucket: str | None = None
    ) -> None:
        assert bucket == "documents-test"

    monkeypatch.setattr(tenant_storage, "document_bucket", _bucket)
    monkeypatch.setattr(storage, "put_document", _put)
    staff = _staff_principal(fx)
    staff_ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=staff)
    out = await api.fulfil_identity_document(
        exec_uid=fx["execution_uid"],
        item_uid=str(item["item_uid"]),
        case_uid=fx["case_uid"],
        step_key="idv",
        participant_contact_id=fx["contact"],
        file=UploadFile(
            filename="passport.pdf",
            file=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"),
        ),
        principal=staff,
        ctx=staff_ctx,
    )
    assert out["ok"] is True
    assert out["document_uid"]
    assert (
        await _scalar(
            sess,
            "SELECT status FROM documents.request_items WHERE uid = :u",
            u=item["item_uid"],
        )
        == "fulfilled"
    )
    # One of two is filed; the check is still open, and still not linked to either item.
    row = (
        (
            await sess.execute(
                text(
                    "SELECT status, request_item_id FROM flows.case_steps "
                    "WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "pending"
    assert row["request_item_id"] is None
    assert (
        await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :i", i=fx["check"])
        == "pending"
    )


@pytest.mark.asyncio
async def test_both_documents_landing_completes_the_check(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manual path IS its two documents. When the second lands — through the adviser's upload
    here, or the client's own fulfilment, both of which trip ``on_item_fulfilled`` — the step
    completes and the KYC row records the pass WITH the documents behind it. Nobody types a
    verdict."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    client_ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    raised = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"),
        principal=_client_principal(fx),
        ctx=client_ctx,
    )
    items = raised["documents"]
    assert isinstance(items, list) and len(items) == 2

    async def _bucket(_session: AsyncSession, _tenant_id: int) -> str:
        return "documents-test"

    async def _put(
        _key: str, _data: bytes, _content_type: str, *, bucket: str | None = None
    ) -> None:
        pass

    monkeypatch.setattr(tenant_storage, "document_bucket", _bucket)
    monkeypatch.setattr(storage, "put_document", _put)
    staff = _staff_principal(fx)
    staff_ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=staff)
    for item, name in zip(items, ("passport.pdf", "bill.pdf"), strict=True):
        await api.fulfil_identity_document(
            exec_uid=fx["execution_uid"],
            item_uid=str(item["item_uid"]),
            case_uid=fx["case_uid"],
            step_key="idv",
            participant_contact_id=fx["contact"],
            file=UploadFile(filename=name, file=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF")),
            principal=staff,
            ctx=staff_ctx,
        )
    step = (
        (
            await sess.execute(
                text(
                    "SELECT status, data ->> 'last_event' AS last_event FROM flows.case_steps "
                    "WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    assert step["status"] == "fulfilled"
    assert step["last_event"] == "identity_documents_received"
    check = (
        (
            await sess.execute(
                text("SELECT status, raw, valid_until FROM crm.kyc_checks WHERE id = :i"),
                {"i": fx["check"]},
            )
        )
        .mappings()
        .one()
    )
    assert check["status"] == "passed"
    assert check["valid_until"] is None  # no horizon: it stands for the next journey
    raw = check["raw"] if isinstance(check["raw"], dict) else json.loads(check["raw"])
    assert raw["method"] == "documents"
    assert sorted(e["key"] for e in raw["evidence"]) == ["photo_id", "proof_of_address"]
    assert all(e["document_uid"] for e in raw["evidence"])


@pytest.mark.asyncio
async def test_an_identity_check_cannot_be_passed_or_referred_by_hand(
    service_session: AsyncSession,
) -> None:
    """Identity is mandatory and is settled by the check itself — the provider's result or the
    documents landing — never by a verdict typed at the step. The journey page offers no Pass /
    Refer, and this is the API side of the same rule, so a ``cases:write`` holder cannot reach the
    transition some other way. Refused BEFORE the evidence is in, and still refused after it — the
    evidence completing the step is the engine's job, not a person's."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))
    raised = await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    items = raised["documents"]
    assert isinstance(items, list)

    async def decide(outcome: str) -> Any:
        return await orchestrator.submit_step(
            sess,
            tenant_id=fx["tenant"],
            case_id=fx["case"],
            step_key="idv",
            detail={"outcome": outcome, "note": "Reviewed"},
            participant_subject_id=fx["contact"],
        )

    for outcome in ("passed", "referred"):
        with pytest.raises(StepTransitionRefused) as refused:
            await decide(outcome)
        assert "not passed or referred by hand" in str(refused.value)

    await sess.execute(
        text("UPDATE documents.request_items SET status = 'fulfilled' WHERE uid = ANY(:u)"),
        {"u": [str(i["item_uid"]) for i in items]},
    )
    with pytest.raises(StepTransitionRefused):
        await decide("passed")
    assert (
        await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :i", i=fx["check"])
        == "pending"
    )


@pytest.mark.asyncio
async def test_a_digital_identity_check_is_settled_by_the_provider_result(
    service_session: AsyncSession,
) -> None:
    """ComplyCube checks the client's photos, and its result arrives through
    ``on_identity_result``, which is not a hand decision and is not refused as one."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    released = await orchestrator.on_identity_result(
        sess, fx["check"], outcome="passed", external_ref="complycube-check-1"
    )
    assert released is True
    assert (
        await _scalar(
            sess,
            "SELECT status FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'",
            c=fx["case"],
        )
        == "fulfilled"
    )
    assert (
        await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :i", i=fx["check"])
        == "passed"
    )


@pytest.mark.asyncio
async def test_one_request_serves_the_whole_journey(service_session: AsyncSession) -> None:
    """The two documents join the client's EXISTING request, not a second one.

    A request is what their portal checklist is — one per client per journey — so a second would
    split their outstanding work across two lists.
    """
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=False)
    # An existing request holding an item this journey's steps already point at.
    request_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.requests (uid, tenant_id, contact_id, priority, message) "
            "VALUES (:u, :t, :c, 'normal', 'Onboarding') RETURNING id",
            u=uuid.uuid4().hex[:12],
            t=fx["tenant"],
            c=fx["contact"],
        )
    )
    item_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.request_items (uid, request_id, tenant_id, action, title) "
            "VALUES (:u, :r, :t, 'complete_form', 'Your details') RETURNING id",
            u=uuid.uuid4().hex[:12],
            r=request_id,
            t=fx["tenant"],
        )
    )
    await sess.execute(
        text(
            "INSERT INTO flows.case_steps "
            "  (tenant_id, case_id, step_id, step_key, step_type, title, assignee, status, "
            "   participant_subject_id, request_item_id) "
            "VALUES (:t, :c, 'core', 'core', 'fill_form', 'Your details', 'client', 'fulfilled', "
            "        :psid, :ri)"
        ),
        {"t": fx["tenant"], "c": fx["case"], "psid": fx["contact"], "ri": item_id},
    )
    # The identity step starts once the earlier steps' items exist, and asks for its photos then:
    # slots on the step, not items on the request.
    await _raise_capture(sess, fx)
    await _claims(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_client_principal(fx))

    await api.my_identity_manual(
        body=IdentityChoice(step_key="idv"), principal=_client_principal(fx), ctx=ctx
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.requests WHERE tenant_id = :t AND contact_id = :c",
            t=fx["tenant"],
            c=fx["contact"],
        )
        == 1
    )
    # The earlier item and the two documents.
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.request_items WHERE request_id = :r",
            r=request_id,
        )
        == 3
    )


@pytest.mark.asyncio
async def test_the_adviser_can_require_a_manual_check_at_kickoff(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`intake.identity_mode = 'manual'` is the adviser's end of the SAME decision.

    An adviser who already knows their client cannot photograph a document should not have to let
    them find that out mid-journey. It writes the same `identity_choice` key the client's own
    choice writes and moves the step to staff, so `_provider` and every surface read one answer.
    ComplyCube is configured here, so the photos WOULD be asked for if the choice were ignored.
    """
    monkeypatch.setattr(settings, "complycube_api_key", "k")
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=False)
    await sess.execute(
        text(
            "UPDATE flows.executions SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb) "
            "WHERE id = (SELECT execution_id FROM flows.cases WHERE id = :c)"
        ),
        {"d": json.dumps({"intake": {"identity_mode": "manual"}}), "c": fx["case"]},
    )
    execution_id = int(
        await _scalar(sess, "SELECT execution_id FROM flows.cases WHERE id = :c", c=fx["case"])
    )
    handler = get_handler("verify_identity")
    ctx = StepContext(
        session=sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        case_id=fx["case"],
        step_key="idv",
        config={"provider": "complycube", "validity_days": 365},
        participant_subject_id=fx["contact"],
    )
    assert await handler._kickoff_requires_manual(ctx) is True

    await handler.instantiate(ctx)

    row = (
        (
            await sess.execute(
                text(
                    "SELECT assignee, data ->> 'identity_choice' AS choice, "
                    "       data -> 'identity_documents' AS docs, request_item_id "
                    "FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["choice"] == "manual"
    assert row["assignee"] == "staff"
    # The two documents are asked for straight away: a client never offered the digital check must
    # still be told what to send, and `manual` is the no-op provider, so without them the step
    # would wait on nothing.
    assert isinstance(row["docs"], dict)
    assert set(row["docs"]) == {"photo_id", "proof_of_address"}
    # Still evidence, not the decision.
    assert row["request_item_id"] is None
    # And no photos were asked for on a check the adviser took off that path.
    assert (
        await _scalar(
            sess,
            "SELECT data -> 'identity_capture' FROM flows.case_steps "
            "WHERE case_id = :c AND step_key = 'idv'",
            c=fx["case"],
        )
        is None
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.request_items WHERE tenant_id = :t",
            t=fx["tenant"],
        )
        == 2
    )
    # The check the step opened is recorded as manual: that is who runs it.
    assert (
        await _scalar(
            sess,
            "SELECT provider FROM crm.kyc_checks WHERE id = :i",
            i=ctx.data["kyc_check_id"],
        )
        == "manual"
    )


@pytest.mark.asyncio
async def test_without_a_kickoff_choice_a_complycube_step_asks_for_photos(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the case above: the same step, with nothing required at kickoff,
    asks the client for the three photos (slots on the step, not checklist items) and leaves the
    choice to them."""
    monkeypatch.setattr(settings, "complycube_api_key", "k")
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=False)
    handler = get_handler("verify_identity")
    ctx = StepContext(
        session=sess,
        tenant_id=fx["tenant"],
        execution_id=fx["execution"],
        case_id=fx["case"],
        step_key="idv",
        config={"provider": "complycube", "validity_days": 365},
        participant_subject_id=fx["contact"],
    )

    await handler.instantiate(ctx)

    row = (
        (
            await sess.execute(
                text(
                    "SELECT assignee, data ->> 'identity_choice' AS choice, "
                    "       data -> 'identity_capture' AS capture "
                    "FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'"
                ),
                {"c": fx["case"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["choice"] is None
    assert row["assignee"] == "client"
    capture = row["capture"]
    assert isinstance(capture, dict)
    assert set(idv_capture.item_uids(capture)) == set(idv_capture.KEYS)
    assert {k: capture[k] for k in ("attempt", "status", "last_reason", "document_type")} == {
        "attempt": 1,
        "status": "capturing",
        "last_reason": None,
        "document_type": None,
    }
    assert await _request_items(sess, fx) == []
    assert (
        await _scalar(
            sess,
            "SELECT provider FROM crm.kyc_checks WHERE id = :i",
            i=ctx.data["kyc_check_id"],
        )
        == "complycube"
    )


@pytest.mark.asyncio
async def test_kickoff_identity_choice_is_scoped_to_the_person(
    service_session: AsyncSession,
) -> None:
    """A joint journey may send one person through Auto IDV and another through Manual IDV."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    execution_id = int(
        await _scalar(sess, "SELECT execution_id FROM flows.cases WHERE id = :c", c=fx["case"])
    )
    await sess.execute(
        text("UPDATE flows.executions SET data = CAST(:d AS jsonb) WHERE id = :e"),
        {
            "e": execution_id,
            "d": json.dumps(
                {
                    "intake": {
                        "identity_mode": "default",
                        "identity_modes": {str(fx["contact"]): "manual"},
                    }
                }
            ),
        },
    )
    handler = get_handler("verify_identity")
    own = StepContext(
        session=sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        case_id=fx["case"],
        step_key="idv",
        config={},
        participant_subject_id=fx["contact"],
    )
    other = StepContext(
        session=sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        case_id=fx["case"],
        step_key="idv",
        config={},
        participant_subject_id=int(fx["contact"]) + 1,
    )
    assert await handler._kickoff_requires_manual(own) is True
    assert await handler._kickoff_requires_manual(other) is False


@pytest.mark.asyncio
async def test_asking_twice_does_not_ask_for_four_documents(
    service_session: AsyncSession,
) -> None:
    """Both doors reach `raise_request`, and a re-instantiate reaches it again."""
    sess = service_session
    fx = await _seed_client_identity_step(sess)
    execution_id = int(
        await _scalar(sess, "SELECT execution_id FROM flows.cases WHERE id = :c", c=fx["case"])
    )
    first = await idv_manual.raise_request(
        sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        contact_id=fx["contact"],
        case_id=fx["case"],
        step_key="idv",
    )
    again = await idv_manual.raise_request(
        sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        contact_id=fx["contact"],
        case_id=fx["case"],
        step_key="idv",
    )
    assert [d["item_uid"] for d in again] == [d["item_uid"] for d in first]
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.request_items "
            "WHERE tenant_id = :t AND title = ANY(:titles)",
            t=fx["tenant"],
            titles=[spec.title for spec in idv_manual.DOCUMENTS],
        )
        == 2
    )


@pytest.mark.asyncio
async def test_the_clients_checklist_is_reopened_so_they_can_send_them(
    service_session: AsyncSession,
) -> None:
    """The identity step sits after the fact-find and terms, so by the time it asks, the client's
    request has usually rolled up to `fulfilled` — and every surface that offers an upload gates on
    `requests.status = 'pending'`. Two new items on a completed request are visible and
    unfulfillable, which is no better than not asking. (The adviser required a manual check at
    kickoff here, so no photos were asked for first.)"""
    sess = service_session
    fx = await _seed_client_identity_step(sess, capture=False)
    execution_id = int(
        await _scalar(sess, "SELECT execution_id FROM flows.cases WHERE id = :c", c=fx["case"])
    )
    request_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.requests "
            "  (uid, tenant_id, contact_id, priority, message, status, completed_at) "
            "VALUES (:u, :t, :c, 'normal', 'Onboarding', 'fulfilled', now()) RETURNING id",
            u=uuid.uuid4().hex[:12],
            t=fx["tenant"],
            c=fx["contact"],
        )
    )
    item_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.request_items "
            "  (uid, request_id, tenant_id, action, title, status) "
            "VALUES (:u, :r, :t, 'complete_form', 'Your details', 'fulfilled') RETURNING id",
            u=uuid.uuid4().hex[:12],
            r=request_id,
            t=fx["tenant"],
        )
    )
    await sess.execute(
        text(
            "INSERT INTO flows.case_steps "
            "  (tenant_id, case_id, step_id, step_key, step_type, title, assignee, status, "
            "   participant_subject_id, request_item_id) "
            "VALUES (:t, :c, 'core', 'core', 'fill_form', 'Your details', 'client', 'fulfilled', "
            "        :psid, :ri)"
        ),
        {"t": fx["tenant"], "c": fx["case"], "psid": fx["contact"], "ri": item_id},
    )

    await idv_manual.raise_request(
        sess,
        tenant_id=fx["tenant"],
        execution_id=execution_id,
        contact_id=fx["contact"],
        case_id=fx["case"],
        step_key="idv",
    )
    row = (
        (
            await sess.execute(
                text("SELECT status, completed_at FROM documents.requests WHERE id = :r"),
                {"r": request_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "pending"
    assert row["completed_at"] is None
