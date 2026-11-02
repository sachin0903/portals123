"""The client is asked for their signed W-9 / W-8 when Additional information records one.

The seeded onboarding remembers the W-9 / W-8 answer (``additional_info.config.remember``). When
the declaration is sent, the fork ``needs_fatca_form`` reads it in each client's profile case: a
W-9 or a W-8 releases ``fatca_form``, a client upload of category ``fatca_form``; anything else
skips it. Opening the account waits for the form; nothing else does.

The pure half pins the seed's shape and migration 0262's patch. The rest drives the orchestrator
and the real upload doors against Postgres (Docker), on the runtime suite's bed: one individual,
no accounts, so the profile case and the declaration's application case are the whole journey.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.auth import Principal
from app.db import FirmContext
from app.flows import graph as graph_mod
from app.flows import memory as memory_mod
from app.flows import onboarding_seed, onboarding_terms, planning
from app.flows.definitions import publish_issues, validate_definition
from app.routers import crm as crm_router
from app.routers import documents as documents_router
from app.routers import firm as firm_router
from tests.test_flows_graph_runtime import _Bed, _bed, _complete, _declare, _start

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations/versions"
_spec = importlib.util.spec_from_file_location(
    "m0262", _MIGRATION / "0262_onboarding_fatca_upload.py"
)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
_TEXT = b"I signed my W-9."


def _seed_args() -> tuple[dict[str, str], dict[str, str]]:
    return (
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {code: f"doc_{code}" for code in onboarding_terms.TERMS},
    )


def _seed() -> dict[str, Any]:
    """The onboarding journey as a firm is seeded with it now."""
    return validate_definition(onboarding_seed._build_definition(*_seed_args()))


def _before() -> dict[str, Any]:
    """The same journey as it was published before the upload: no fork, no step, no remember."""
    raw = onboarding_seed._build_definition(*_seed_args())
    raw["nodes"] = [n for n in raw["nodes"] if n["id"] not in ("needs_fatca_form", "fatca_form")]
    for node in raw["nodes"]:
        if node["id"] == "declaration":
            node["next"] = "terms_general"
        if node["id"] == "additional_info":
            node["config"] = {k: v for k, v in node["config"].items() if k != "remember"}
    return validate_definition(raw)


# ---- the seeded journey ------------------------------------------------------------------------


def test_additional_information_remembers_only_the_w9_w8_answer() -> None:
    steps = {s["id"]: s for s in _seed()["steps"]}
    remember = steps["additional_info"]["config"]["remember"]
    assert remember == {"fatca_status": "fatca_status"}
    answers = {"us_person": "Yes", "fatca_status": "w9", "source_of_wealth": "salary"}
    assert memory_mod.promotions(remember, answers) == {"fatca_status": "w9"}


@pytest.mark.parametrize("answer", ["w9", "w8"])
def test_a_w9_or_a_w8_takes_the_fork_to_the_upload(answer: str) -> None:
    parsed = graph_mod.parse(_seed())
    facts = {"memory": {"fatca_status": answer}}
    assert graph_mod.branch_choice(parsed, "needs_fatca_form", facts) == "fatca_form"


@pytest.mark.parametrize("answer", [None, "", "w8_cure", "other_individual", "not_certified"])
def test_any_other_answer_goes_straight_on_and_skips_the_upload(answer: str | None) -> None:
    parsed = graph_mod.parse(_seed())
    facts: dict[str, Any] = {"memory": {"fatca_status": answer}}
    assert graph_mod.branch_choice(parsed, "needs_fatca_form", facts) == "setup"
    assert graph_mod.branch_choice(parsed, "needs_fatca_form", {}) == "setup"
    assert planning.elided_by_branch(parsed, "needs_fatca_form", "setup") == ("fatca_form",)


def test_the_upload_follows_the_declaration_and_only_opening_the_account_waits_for_it() -> None:
    seed = _seed()
    steps = {s["id"]: s for s in seed["steps"]}
    upload = steps["fatca_form"]
    assert (upload["type"], upload["scope"], upload["assignee"]) == (
        "upload_document",
        "participant",
        "client",
    )
    assert upload["config"] == {
        "category": "fatca_form",
        "format_hint": "PDF or a photo",
        "title": "Upload your signed W-9 / W-8",
    }
    # Asked of the same people the question is asked of.
    assert upload["visibility"] == steps["additional_info"]["visibility"]
    assert upload["depends_on"] == ["declaration"]
    waiting = sorted(sid for sid, s in steps.items() if "fatca_form" in s["depends_on"])
    assert waiting == ["setup"]
    assert publish_issues(seed) == []


# ---- migration 0262's patch ----------------------------------------------------------------------


def test_the_patch_turns_the_published_journey_into_the_seed() -> None:
    """Exactly the seed, so a re-seed after the migration publishes nothing new."""
    assert migration.patched(_before()) == _seed()


def test_the_patch_applies_once() -> None:
    assert migration.patched(_seed()) is None


def test_the_patch_leaves_a_journey_it_cannot_place_the_upload_in() -> None:
    before = _before()
    without_info = {
        **before,
        "nodes": [n for n in before["nodes"] if n["id"] != "additional_info"],
    }
    assert migration.patched(without_info) is None
    without_setup = {**before, "nodes": [n for n in before["nodes"] if n["id"] != "setup"]}
    assert migration.patched(without_setup) is None
    # A legacy definition has no forks to add.
    legacy = validate_definition(onboarding_seed._legacy_definition(*_seed_args()))
    assert migration.patched(legacy) is None
    assert migration.patched(None) is None


def test_the_patch_keeps_a_firms_own_wiring_and_memory() -> None:
    raw = onboarding_seed._build_definition(*_seed_args())
    raw["nodes"] = [n for n in raw["nodes"] if n["id"] not in ("needs_fatca_form", "fatca_form")]
    for node in raw["nodes"]:
        if node["id"] == "declaration":
            node["next"] = ["terms_general", "idv"]
        if node["id"] == "additional_info":
            node["config"] = {**node["config"], "remember": {"wealth": "source_of_wealth"}}
    patched = migration.patched(validate_definition(raw))
    assert patched is not None
    nodes = {n["id"]: n for n in patched["nodes"]}
    assert nodes["declaration"]["next"] == ["terms_general", "idv", "needs_fatca_form"]
    assert nodes["additional_info"]["config"]["remember"] == {
        "wealth": "source_of_wealth",
        "fatca_status": "fatca_status",
    }
    # What is stored is what the validator would store.
    assert validate_definition(patched) == patched


# ---- the runtime ---------------------------------------------------------------------------------


def _fake_document_store(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bucket(_session: AsyncSession, _tenant_id: int) -> str:
        return "documents-test"

    async def _put(
        _key: str, _data: bytes, _content_type: str, *, bucket: str | None = None
    ) -> None:
        return None

    monkeypatch.setattr(tenant_storage, "document_bucket", _bucket)
    monkeypatch.setattr(storage, "put_document", _put)


def _file(data: bytes = _PDF, name: str = "w9-signed.pdf") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(data), size=len(data))


def _staff(sess: AsyncSession, bed: _Bed) -> FirmContext:
    return FirmContext(
        session=sess,
        tenant_id=bed.tenant,
        principal=Principal(user_id="wos_staff", role="adviser", permissions=["contacts:write"]),
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _answered(
    sess: AsyncSession, answer: str | None, *, us_person: bool = True
) -> tuple[_Bed, int, int]:
    """A journey whose Additional information recorded ``answer``, up to the declaration being
    sent. The record holds the answer too, as the form's write-back leaves it. Returns the bed,
    the execution id and the profile case id."""
    bed = await _bed(sess)
    await sess.execute(
        text("UPDATE crm.contacts SET us_person = :us, fatca_status = :fs WHERE id = :c"),
        {"us": us_person, "fs": answer or None, "c": bed.contact},
    )
    execution_id, case_id = await _start(sess, bed, _seed())
    await _complete(sess, bed, case_id, "core")
    # What `orchestrator._promote_memory` does with the seed's `remember` when the form completes.
    await memory_mod.promote_from_answers(
        sess,
        tenant_id=bed.tenant,
        case_id=case_id,
        mapping=onboarding_seed.FATCA_REMEMBER,
        answers={"us_person": "Yes", "fatca_status": answer} if answer is not None else {},
        node_id="additional_info",
    )
    await _complete(sess, bed, case_id, "additional_info")
    return bed, execution_id, case_id


async def _upload_step(sess: AsyncSession, case_id: int) -> dict[str, Any]:
    row = (
        (
            await sess.execute(
                text(
                    "SELECT cs.status, cs.assignee, cs.data, ri.id AS item_id, ri.uid AS item_uid, "
                    "       ri.status AS item_status, ri.action, ri.category, ri.title, "
                    "       ri.format_hint, ri.fulfilled_document_id, r.uid AS request_uid, "
                    "       r.contact_id "
                    "FROM flows.case_steps cs "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
                    "WHERE cs.case_id = :c AND cs.step_key = 'fatca_form'"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _client_uploads(
    sess: AsyncSession, bed: _Bed, step: dict[str, Any], file: UploadFile
) -> None:
    """The client's own door (`POST /documents/requests/{r}/items/{i}/fulfill`), as their login."""
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"tenant_id": bed.tenant, "contact_ids": [bed.contact]})},
    )
    await documents_router.fulfill_request_item(
        str(step["request_uid"]),
        str(step["item_uid"]),
        file=file,
        ctx=FirmContext(
            session=sess,
            tenant_id=bed.tenant,
            principal=Principal(user_id="wos_client", role="client", permissions=[]),
        ),
    )


async def _linked(sess: AsyncSession, contact: int) -> Any:
    return await _scalar(
        sess, "SELECT fatca_document_id FROM crm.contacts WHERE id = :c", c=contact
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["w9", "w8"])
async def test_a_w9_or_w8_answer_asks_the_client_once_the_declaration_is_sent(
    service_session: AsyncSession, answer: str
) -> None:
    sess = service_session
    bed, _, case_id = await _answered(sess, answer, us_person=answer == "w9")

    # Held behind the fork until the adviser hands the journey over.
    held = await _upload_step(sess, case_id)
    assert held["status"] == "pending"
    assert held["item_id"] is None

    await _declare(sess, bed, case_id)
    step = await _upload_step(sess, case_id)
    assert step["status"] == "pending"
    assert step["assignee"] == "client"
    assert step["contact_id"] == bed.contact
    assert (step["action"], step["category"], step["item_status"]) == (
        "upload",
        "fatca_form",
        "pending",
    )
    assert step["title"] == "Upload your signed W-9 / W-8"
    assert step["format_hint"] == "PDF or a photo"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, "w8_cure"])
async def test_no_w9_or_w8_answer_skips_the_upload(
    service_session: AsyncSession, answer: str | None
) -> None:
    sess = service_session
    bed, _, case_id = await _answered(sess, answer, us_person=False)
    await _declare(sess, bed, case_id)
    step = await _upload_step(sess, case_id)
    assert step["status"] == "skipped"
    assert step["item_id"] is None


@pytest.mark.asyncio
async def test_the_clients_upload_completes_the_step_and_is_linked_to_their_answer(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    bed, _, case_id = await _answered(sess, "w9")
    await _declare(sess, bed, case_id)

    await _client_uploads(sess, bed, await _upload_step(sess, case_id), _file())

    step = await _upload_step(sess, case_id)
    assert step["status"] == "fulfilled"
    assert step["item_status"] == "fulfilled"
    assert await _linked(sess, bed.contact) == step["fulfilled_document_id"]
    document_type = await _scalar(
        sess,
        "SELECT document_type FROM documents.documents WHERE id = :d",
        d=step["fulfilled_document_id"],
    )
    assert document_type == "fatca_form"


@pytest.mark.asyncio
async def test_an_upload_that_is_not_a_pdf_or_an_image_is_refused(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    bed, _, case_id = await _answered(sess, "w9")
    await _declare(sess, bed, case_id)

    with pytest.raises(HTTPException) as refused:
        await _client_uploads(sess, bed, await _upload_step(sess, case_id), _file(_TEXT, "w9.txt"))
    assert refused.value.status_code == 422
    assert refused.value.detail == (
        "The signed copy needs to be a PDF or an image (a scan or a photograph)."
    )
    step = await _upload_step(sess, case_id)
    assert (step["status"], step["item_status"]) == ("pending", "pending")
    assert await _linked(sess, bed.contact) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("held", "links"), [("w9", True), (None, False)])
async def test_a_requested_form_is_linked_only_while_the_answer_is_w9_or_w8(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, held: str | None, links: bool
) -> None:
    """The same door serves a request staff raised by hand. A form sent for a client whose answer
    is not a W-9 or a W-8 stays in their documents and is not linked."""
    sess = service_session
    _fake_document_store(monkeypatch)
    bed = await _bed(sess)
    await sess.execute(
        text("UPDATE crm.contacts SET us_person = true, fatca_status = :fs WHERE id = :c"),
        {"fs": held, "c": bed.contact},
    )
    s = uuid.uuid4().hex[:12]
    request_id = await _scalar(
        sess,
        "INSERT INTO documents.requests (uid, tenant_id, contact_id, priority, message) "
        "VALUES (:u, :t, :c, 'normal', 'Your W-9') RETURNING id",
        u=s,
        t=bed.tenant,
        c=bed.contact,
    )
    await sess.execute(
        text(
            "INSERT INTO documents.request_items (uid, request_id, tenant_id, action, title, "
            "  category) VALUES (:u, :r, :t, 'upload', 'Your signed W-9', 'fatca_form')"
        ),
        {"u": f"i{s[1:]}", "r": request_id, "t": bed.tenant},
    )
    await _client_uploads(sess, bed, {"request_uid": s, "item_uid": f"i{s[1:]}"}, _file())

    filed = await _scalar(
        sess,
        "SELECT fulfilled_document_id FROM documents.request_items WHERE uid = :u",
        u=f"i{s[1:]}",
    )
    assert filed is not None
    assert (await _linked(sess, bed.contact) == filed) is links


@pytest.mark.asyncio
async def test_staff_filing_the_form_on_the_record_completes_the_clients_step(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client handed the form over: the record's upload answers the open request, so the
    journey does not keep asking the client for a form the firm holds."""
    sess = service_session
    _fake_document_store(monkeypatch)
    bed, _, case_id = await _answered(sess, "w9")
    await _declare(sess, bed, case_id)

    filed = await crm_router.upload_contact_fatca_document(
        contact_id=bed.contact, file=_file(), ctx=_staff(sess, bed)
    )

    step = await _upload_step(sess, case_id)
    assert (step["status"], step["item_status"]) == ("fulfilled", "fulfilled")
    document_uid = await _scalar(
        sess, "SELECT uid FROM documents.documents WHERE id = :d", d=step["fulfilled_document_id"]
    )
    assert document_uid == filed.fatca_document.uid
    assert await _linked(sess, bed.contact) == step["fulfilled_document_id"]


@pytest.mark.asyncio
async def test_a_form_already_on_the_record_completes_the_step_when_it_is_raised(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filed during the fact-find, before the declaration: the fork still takes the upload, and
    the step completes with the form the record holds instead of asking the client."""
    sess = service_session
    _fake_document_store(monkeypatch)
    bed, _, case_id = await _answered(sess, "w9")
    filed = await crm_router.upload_contact_fatca_document(
        contact_id=bed.contact, file=_file(), ctx=_staff(sess, bed)
    )

    await _declare(sess, bed, case_id)

    step = await _upload_step(sess, case_id)
    assert (step["status"], step["item_status"]) == ("fulfilled", "fulfilled")
    document_uid = await _scalar(
        sess, "SELECT uid FROM documents.documents WHERE id = :d", d=step["fulfilled_document_id"]
    )
    assert document_uid == filed.fatca_document.uid


@pytest.mark.asyncio
async def test_an_archived_form_on_the_record_does_not_complete_the_step(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record shows an archived form as not held, so the step asks the client for a new one."""
    sess = service_session
    _fake_document_store(monkeypatch)
    bed, _, case_id = await _answered(sess, "w9")
    filed = await crm_router.upload_contact_fatca_document(
        contact_id=bed.contact, file=_file(), ctx=_staff(sess, bed)
    )
    await sess.execute(
        text("UPDATE documents.documents SET status = 'archived' WHERE uid = :u"),
        {"u": filed.fatca_document.uid},
    )

    await _declare(sess, bed, case_id)

    step = await _upload_step(sess, case_id)
    assert step["item_status"] == "pending"


@pytest.mark.asyncio
async def test_clearing_the_answer_on_the_record_withdraws_the_open_step(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed, _, case_id = await _answered(sess, "w9")
    await _declare(sess, bed, case_id)

    async def patch(body: dict[str, object]) -> None:
        await firm_router.update_contact(
            contact_uid=str(bed.contact),
            contact_id=bed.contact,
            body=firm_router.UpdateContact.model_validate(body),
            ctx=_staff(sess, bed),
        )

    # W-9 to W-8 still needs a signed form: the step stays open.
    await patch({"us_person": False, "fatca_status": "w8"})
    assert (await _upload_step(sess, case_id))["item_status"] == "pending"

    await patch({"fatca_status": None})
    step = await _upload_step(sess, case_id)
    assert step["status"] == "skipped"
    assert step["data"]["skipped_reason"] == "fatca_status_changed"
    assert step["item_status"] == "cancelled"
    assert (
        await _scalar(
            sess,
            "SELECT payload ->> 'step_key' FROM flows.domain_events "
            "WHERE case_id = :c AND event_type = 'step.withdrawn'",
            c=case_id,
        )
        == "fatca_form"
    )
    # Withdrawn work does not hold the client's case open: identity and AML are what is left.
    await _complete(sess, bed, case_id, "idv")
    await _complete(sess, bed, case_id, "aml")
    assert (
        await _scalar(sess, "SELECT status FROM flows.cases WHERE id = :c", c=case_id)
        == "completed"
    )


# ---- migration 0262 against Postgres -------------------------------------------------------------


def _onboarding_flow(conn: Any, definition: dict[str, Any], *, prefix: str) -> tuple[int, int]:
    """A firm whose onboarding flow's current published version is ``definition``."""
    s = uuid.uuid4().hex[:10]
    tenant = conn.execute(
        text("INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, 'M0262') RETURNING id"),
        {"u": f"ten_{prefix}{s}", "a": f"{prefix}-{s}"},
    ).scalar_one()
    flow = conn.execute(
        text(
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, 'onboarding', 'Onboarding', 'published') RETURNING id"
        ),
        {"u": f"f{s}x", "t": tenant},
    ).scalar_one()
    version = conn.execute(
        text(
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
        ),
        {"u": f"v{s}x", "t": tenant, "f": flow, "d": json.dumps(definition)},
    ).scalar_one()
    conn.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version, "f": flow},
    )
    return int(flow), int(version)


def _current(conn: Any, flow: int) -> tuple[int, int, dict[str, Any]]:
    row = conn.execute(
        text(
            "SELECT fv.id, fv.version, fv.definition FROM flows.flows f "
            "JOIN flows.flow_versions fv ON fv.id = f.current_published_version_id "
            "WHERE f.id = :f"
        ),
        {"f": flow},
    ).one()
    return int(row.id), int(row.version), row.definition


def test_the_migration_publishes_the_upload_as_a_new_version_and_downgrades(
    db: dict[str, str], super_engine: Engine
) -> None:
    other = _before()
    other["nodes"] = [n for n in other["nodes"] if n["id"] != "additional_info"]
    with super_engine.connect() as conn:
        patched_flow, original = _onboarding_flow(conn, _before(), prefix="mpat")
        untouched_flow, untouched = _onboarding_flow(conn, other, prefix="mskip")

    migrator = create_engine(db["migrator"])
    try:
        with migrator.connect() as conn:
            assert migration.apply(conn) >= 1
            # Twice: a flow already on the new version is skipped.
            conn.exec_driver_sql("SET LOCAL ROLE service_role")
            moved_id, moved_version, moved = _current(conn, patched_flow)
            conn.exec_driver_sql("RESET ROLE")
            migration.apply(conn)
            conn.exec_driver_sql("SET LOCAL ROLE service_role")
            again = _current(conn, patched_flow)
            skipped = _current(conn, untouched_flow)
            kept = conn.execute(
                text("SELECT count(*) FROM flows.flow_versions WHERE id = :v"), {"v": original}
            ).scalar_one()
            conn.exec_driver_sql("RESET ROLE")

            assert migration.revert(conn) >= 1
            conn.exec_driver_sql("SET LOCAL ROLE service_role")
            restored = _current(conn, patched_flow)
            gone = conn.execute(
                text("SELECT count(*) FROM flows.flow_versions WHERE id = :v"), {"v": moved_id}
            ).scalar_one()
            conn.rollback()
    finally:
        migrator.dispose()

    assert moved_version == 2
    assert moved == _seed()
    assert again[0] == moved_id
    assert skipped[0] == untouched
    # The version a running journey is pinned to stays as it was.
    assert kept == 1
    assert restored[0] == original
    assert gone == 0
