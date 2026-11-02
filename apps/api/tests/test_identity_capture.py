"""The digital identity check: the client's own photos, the ComplyCube check they start, and every
way that check ends.

A ``verify_identity`` step on ComplyCube (``provider: complycube``, or a retired ``idenfy`` pin on
a version already published) asks the client for three photos as slots on the step. The client's
submit carries the photos and names the document: each photo is checked against what ComplyCube
takes, held sealed in ``compliance.identity_photos`` (``app/idv/held.py``), and the send is queued
as ``identity.check.requested``. The worker sends them to ComplyCube and starts the check
(``app.complycube.identity.submit``); a ComplyCube outage raises, so the outbox sends again.
Nothing reaches object storage or ``documents.*``, and our ``compliance`` rows hold ComplyCube's
ids only. A held photo goes as soon as nothing needs it: when its try reopens it, and when the
identity step finishes. The result is routed back to the step (``app.complycube.routing``):

* clear → the step passes;
* rejected or referred, on any try → held for an operator; clear passes, reject fails, and "Ask for
  new photos" opens the next try for only the photos chosen, told only a client-safe reason;
* ComplyCube could not run it → the same photos are sent again by themselves, once per try, and
  the client is asked for nothing; a second failure on the try goes to operations;
* ComplyCube refusing an upload → at the SAME try, what it refused (the selfie, or both sides of
  the document) and every photo neither it nor our copy holds; what it accepted, and a photo we
  hold a copy of, is kept and reused; its message is kept for operations;
* ComplyCube losing a document it held → made again from our copy.

The routing decisions and the capture's rules are unit-tested first (no database); the rest runs
against real Postgres with a fake ComplyCube. The last section is the journey's AML step: a
confirmed screening match holds it until an operator allows the journey to continue.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, cast, get_args

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, storage, tenant_storage
from app.auth import Principal
from app.complycube import decisions, identity, routing, tasks
from app.complycube.client import ComplyCubeNotConfigured, ScreeningClient
from app.config import settings
from app.db import FirmContext
from app.flows import dispatcher, notifications, providers
from app.flows import step_handlers as step_handlers  # noqa: F401 - registers the handlers
from app.flows.dispatcher import PermanentDispatchError
from app.flows.domain_events import StepSignal
from app.flows.handlers import StepContext, get_handler
from app.idv import capture
from app.idv import held as idv_held
from app.permissions import CASES_READ, CONTACTS_READ_ALL, TENANT_PORTAL_READ
from app.routers import aml as aml_router
from app.routers import applications as api
from tests.conftest import SECCL_PLATFORM_SQL

#: Each photo is a real file's magic bytes padded past ComplyCube's 34 KB floor.
PADDING = 40 * 1024


def _jpeg(mark: bytes) -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + mark + b"\x00" * PADDING + b"\xff\xd9"


def _png(mark: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + mark + b"\x00" * PADDING


FRONT_JPEG = _jpeg(b"front")
BACK_JPEG = _jpeg(b"back")
SELFIE_JPEG = _jpeg(b"selfie")
BACK_PNG = _png(b"back")
SELFIE_PNG = _png(b"selfie")
PDF = b"%PDF-1.4\n" + b"0" * PADDING + b"\n%%EOF"
GIF = b"GIF89a" + b"\x00" * PADDING

#: The file the client sends for each photo, unless a test names another: ``(bytes, file name)``.
PHOTOS: dict[str, tuple[bytes, str]] = {
    capture.FRONT: (FRONT_JPEG, "front.jpg"),
    capture.BACK: (BACK_JPEG, "back.jpg"),
    capture.SELFIE: (SELFIE_JPEG, "selfie.jpg"),
}


# ── Unit: what the client is told ─────────────────────────────────────────────────────────────


def test_a_passport_has_no_back() -> None:
    assert capture.needed_keys("passport") == (capture.FRONT, capture.SELFIE)
    assert capture.needed_keys("driving_licence") == capture.KEYS
    assert capture.needed_keys("national_id") == capture.KEYS


def test_every_reason_code_has_its_client_text() -> None:
    assert set(get_args(identity.PhotoReason)) == set(identity.PHOTO_REQUEST_REASONS)
    assert identity.PHOTO_REQUEST_REASONS["none"] is None


def test_no_reason_a_client_reads_describes_a_fraud_signal_or_a_face_mismatch() -> None:
    for told in identity.PHOTO_REQUEST_REASONS.values():
        if told is None:
            continue
        for word in ("fraud", "tamper", "fake", "altered", "match", "liveness", "suspic"):
            assert word not in told.lower()


@pytest.mark.pkgs_ui_pin
def test_the_admin_console_mirrors_the_reasons_word_for_word() -> None:
    mirror = (Path(__file__).parents[2] / "admin" / "src" / "lib" / "aml.ts").read_text(
        encoding="utf-8"
    )
    for code, told in identity.PHOTO_REQUEST_REASONS.items():
        assert re.search(rf"\b{code}\b", mirror)
        if told is not None:
            assert told in mirror


def test_every_file_a_photo_may_be_is_named_for_complycube() -> None:
    assert identity.FILE_EXTENSIONS == {
        "image/jpeg": "jpg",
        "image/png": "png",
        "application/pdf": "pdf",
    }
    for shot in capture.SHOTS:
        assert shot.content_types <= set(identity.FILE_EXTENSIONS)


def test_complycube_takes_each_photo_between_34_kb_and_4_mb() -> None:
    front = capture.SHOT_BY_KEY[capture.FRONT]
    selfie = capture.SHOT_BY_KEY[capture.SELFIE]
    smallest, largest = capture.MIN_PHOTO_BYTES, capture.MAX_PHOTO_BYTES
    assert smallest == 34 * 1024 and largest == 4_000_000
    for content_type in ("image/jpeg", "image/png", "application/pdf"):
        assert front.refusal_for(content_type, smallest) is None
        assert front.refusal_for(content_type, largest) is None
    assert selfie.refusal_for("image/png", smallest) is None
    assert front.refusal_for("image/jpeg", smallest - 1) == front.size_refusal
    assert front.refusal_for("image/jpeg", largest + 1) == front.size_refusal
    assert (
        front.size_refusal == "The photo of your document's front must be between 34 KB and 4 MB."
    )
    assert selfie.size_refusal == "The photo of your face must be between 34 KB and 4 MB."


def test_the_face_must_be_an_image_and_a_document_side_an_image_or_a_pdf() -> None:
    front = capture.SHOT_BY_KEY[capture.FRONT]
    selfie = capture.SHOT_BY_KEY[capture.SELFIE]
    face_told = "The photo of your face must be a JPG or PNG."
    document_told = "Upload your document as a JPG, PNG or PDF."
    for content_type in ("application/pdf", "image/gif", "image/heic", ""):
        assert selfie.refusal_for(content_type, capture.MIN_PHOTO_BYTES) == face_told
    for content_type in ("image/gif", "text/plain", ""):
        assert front.refusal_for(content_type, capture.MIN_PHOTO_BYTES) == document_told
    # The type is judged first: a tiny PDF of a face is told it must be an image.
    assert selfie.refusal_for("application/pdf", 10) == face_told


def test_a_refusal_without_a_message_keeps_its_status() -> None:
    refusal = identity.refused_upload(httpx.Response(400, text="Bad Request"))
    assert refusal["message"] is None
    assert refusal["status_code"] == 400
    assert refusal["at"]


def test_the_retired_identity_and_aml_names_resolve_to_complycube() -> None:
    assert type(providers.get_provider("idenfy", "verify_identity")).__name__ == (
        "ComplyCubeIdentityProvider"
    )
    assert type(providers.get_provider("creditsafe", "aml_check")).__name__ == (
        "ComplyCubeAmlProvider"
    )
    assert providers.get_provider("idenfy", "aml_check").name == "manual"


# ── Unit: what ComplyCube holds of a try ──────────────────────────────────────────────────────


def _slots(**extra: Any) -> dict[str, Any]:
    """A capture's state: the first try's slots and anything ``extra`` sets."""
    state: dict[str, Any] = {
        capture.FRONT: "f1",
        capture.BACK: "b1",
        capture.SELFIE: "s1",
        "attempt": 1,
        "status": capture.CAPTURING,
        "last_reason": None,
        "document_type": None,
    }
    state.update(extra)
    return state


def test_a_slot_is_held_only_while_complycube_holds_its_photo_for_this_try() -> None:
    sides = {capture.FRONT: "f1", capture.BACK: "b1"}
    held = _slots(**{capture.PROGRESS_KEY: {"accepted": sides, "document_id": "doc"}})
    assert capture.held_keys(held) == {capture.FRONT, capture.BACK}
    # A side accepted for a slot the try reopened is not held.
    reopened = _slots(**{capture.FRONT: "f2", capture.PROGRESS_KEY: held[capture.PROGRESS_KEY]})
    assert capture.held_keys(reopened) == {capture.BACK}
    # Sides with no document to hold them, or a selfie with no live photo, are not held.
    assert capture.held_keys(_slots(**{capture.PROGRESS_KEY: {"accepted": sides}})) == set()
    selfie = {"accepted": {capture.SELFIE: "s1"}}
    assert capture.held_keys(_slots(**{capture.PROGRESS_KEY: selfie})) == set()
    with_photo = {**selfie, "live_photo_id": "lp"}
    assert capture.held_keys(_slots(**{capture.PROGRESS_KEY: with_photo})) == {capture.SELFIE}
    assert capture.held_keys(_slots()) == set()


def test_the_items_list_what_is_held_and_leave_out_a_held_passports_back() -> None:
    fresh = capture.items_of(_slots())
    assert fresh == [
        {"key": capture.FRONT, "item_uid": "f1", "status": "open"},
        {"key": capture.BACK, "item_uid": "b1", "status": "open"},
        {"key": capture.SELFIE, "item_uid": "s1", "status": "open"},
    ]
    passport = _slots(
        document_type="passport",
        **{
            capture.PROGRESS_KEY: {
                "accepted": {capture.FRONT: "f1"},
                "document_id": "doc",
                "document_type": "passport",
            }
        },
    )
    assert capture.items_of(passport) == [
        {"key": capture.FRONT, "item_uid": "f1", "status": "fulfilled"},
        {"key": capture.SELFIE, "item_uid": "s1", "status": "open"},
    ]
    # A photo we hold a copy of is sent too, though ComplyCube does not hold it yet.
    assert capture.items_of(_slots(), frozenset({capture.SELFIE})) == [
        {"key": capture.FRONT, "item_uid": "f1", "status": "open"},
        {"key": capture.BACK, "item_uid": "b1", "status": "open"},
        {"key": capture.SELFIE, "item_uid": "s1", "status": "fulfilled"},
    ]


# ── Unit: where a portal identity result goes ─────────────────────────────────────────────────


def _record(**kw: Any) -> routing.CheckRecord:
    base = routing.CheckRecord(
        id=1,
        uid="chk1",
        tenant_id=10,
        kind="identity",
        origin="portal",
        status="pending",
        kyc_check_id=77,
        contact_id=5,
        monitoring=False,
        provider_check_id="cc_1",
        attempt=1,
    )
    return replace(base, **kw)


#: What ComplyCube holds of the routed check's passport try: its document and its live photo.
HELD_BY_CHECK: dict[str, Any] = {
    "accepted": {capture.FRONT: "f1", capture.SELFIE: "s1"},
    "document_id": "doc",
    "document_type": "passport",
    "live_photo_id": "lp",
}


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, list[Any]]]:
    """Record what routing asks of the capture, the kyc row, the check, the outbox and operations,
    without a database. ``step`` holds the capture step routing finds; a test may replace it."""
    calls: dict[str, list[Any]] = {
        "new_try": [],
        "status": [],
        "settle": [],
        "check": [],
        "sql": [],
        "params": [],
        "resend": [],
        "enqueue": [],
        "failure_task": [],
    }
    calls["step"] = [
        capture.CaptureStep(
            tenant_id=10,
            case_id=2,
            step_key="idv",
            contact_id=5,
            state=_slots(status=capture.SUBMITTED, document_type="passport"),
            manual=False,
        )
    ]

    async def capture_step(_session: AsyncSession, _check: routing.CheckRecord) -> Any:
        return calls["step"][0]

    async def new_try(
        _s: Any, _step: Any, *, attempt: int, last_reason: str | None, **_k: Any
    ) -> dict:
        calls["new_try"].append((attempt, last_reason))
        return {}

    async def set_status(_s: Any, _step: Any, status: str) -> None:
        calls["status"].append(status)

    async def settle(_s: Any, _check: Any, outcome: Any) -> bool:
        calls["settle"].append(outcome.status)
        return True

    async def set_check_status(_s: Any, _check: Any, status: str) -> None:
        calls["check"].append(status)

    async def decided(_s: Any, _check: Any) -> bool:
        return False

    async def held_by_check(_s: Any, _check: Any, _state: Any) -> dict[str, Any]:
        return HELD_BY_CHECK

    async def resend(_s: Any, step: Any, *, progress: dict[str, Any]) -> dict[str, Any]:
        calls["resend"].append(progress)
        count = step.state.get(capture.RESENDS_KEY) or 0
        return {
            **step.state,
            "status": capture.SUBMITTED,
            capture.PROGRESS_KEY: progress,
            capture.RESENDS_KEY: count + 1,
        }

    async def enqueue(_s: Any, **kwargs: Any) -> None:
        calls["enqueue"].append(kwargs)

    async def failure_task(_s: Any, check: routing.CheckRecord) -> bool:
        calls["failure_task"].append(check.uid)
        return True

    async def nothing(*_a: Any, **_k: Any) -> Any:
        return 0

    class _Session:
        async def execute(self, statement: Any, *_a: Any, **_k: Any) -> Any:
            calls["sql"].append(str(statement))
            calls["params"].append(_a[0] if _a else None)

            class _R:
                def scalar_one_or_none(self) -> Any:
                    return {"outcome": "rejected"}

            return _R()

    monkeypatch.setattr(routing, "_capture_step", capture_step)
    monkeypatch.setattr(routing, "_held_by_check", held_by_check)
    monkeypatch.setattr(routing, "raise_failure_task", failure_task)
    monkeypatch.setattr(capture, "new_try", new_try)
    monkeypatch.setattr(capture, "set_status", set_status)
    monkeypatch.setattr(capture, "resend", resend)
    monkeypatch.setattr(notifications, "enqueue", enqueue)
    monkeypatch.setattr(routing, "settle_portal_check", settle)
    monkeypatch.setattr(routing, "_set_status", set_check_status)
    monkeypatch.setattr(routing, "_decided_by_operator", decided)
    monkeypatch.setattr(tasks, "close_screening_task", nothing)
    calls["session"] = [_Session()]
    yield calls


@pytest.mark.parametrize("attempt", [1, 2, 3])
async def test_a_rejected_try_waits_for_an_operator_on_any_try(
    routed: dict[str, list[Any]], attempt: int
) -> None:
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(attempt=attempt), "pending", "rejected")
    assert done == ["portal_review"]
    # Held as an undecided refer for the console, the client sees "a person is checking", and
    # nothing is sent back to them or written on the kyc row.
    assert routed["check"] == ["refer"]
    assert routed["status"] == [capture.REVIEW]
    assert routed["new_try"] == [] and routed["settle"] == []
    assert not any("decided_at" in sql for sql in routed["sql"])


@pytest.mark.parametrize("attempt", [1, 2, 3])
async def test_a_referred_try_waits_for_an_operator_on_any_try(
    routed: dict[str, list[Any]], attempt: int
) -> None:
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(attempt=attempt), "pending", "refer")
    assert done == ["portal_review"]
    assert routed["status"] == [capture.REVIEW]
    # Already `refer`: its status is left alone.
    assert routed["check"] == []
    assert routed["new_try"] == [] and routed["settle"] == []
    assert not any("decided_at" in sql for sql in routed["sql"])


async def test_a_technical_failure_sends_the_same_photos_again_by_themselves(
    routed: dict[str, list[Any]],
) -> None:
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(), "pending", "failed")
    assert done == ["photos_resent"]
    # The client is asked for nothing: no new try, no retake, and no new slots.
    assert routed["new_try"] == [] and routed["status"] == []
    # The capture is submitted again with what ComplyCube holds of the try, and its send is queued
    # with the try's own slots at the same attempt.
    assert routed["resend"] == [HELD_BY_CHECK]
    assert routing.RESEND_EVENT == identity.EVENT
    assert routed["enqueue"] == [
        {
            "tenant_id": 10,
            "event_type": identity.EVENT,
            "payload": {
                "contact_id": 5,
                "case_id": 2,
                "step_key": "idv",
                "kyc_check_id": 77,
                "attempt": 1,
                "document_type": "passport",
                "items": {capture.FRONT: "f1", capture.BACK: "b1", capture.SELFIE: "s1"},
            },
        }
    ]
    # The failed check leaves the operators' queue, saying why; nothing is settled or raised.
    closed = [
        params
        for sql, params in zip(routed["sql"], routed["params"], strict=True)
        if "decided_at" in sql
    ]
    assert closed == [{"comment": routing.FRESH_TRY_COMMENT, "id": 1}]
    assert routed["settle"] == [] and routed["failure_task"] == []


async def test_a_second_technical_failure_on_the_same_try_goes_to_operations(
    routed: dict[str, list[Any]],
) -> None:
    assert routing.RESENDS_PER_TRY == 1
    step = routed["step"][0]
    routed["step"][0] = replace(
        step, state={**step.state, capture.RESENDS_KEY: routing.RESENDS_PER_TRY}
    )
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(), "pending", "failed")
    # Sent again once already: an operator looks rather than paying for the check again.
    assert done == ["failure_task"]
    assert routed["failure_task"] == ["chk1"]
    assert routed["resend"] == [] and routed["enqueue"] == [] and routed["new_try"] == []
    assert not any("decided_at" in sql for sql in routed["sql"])


@pytest.mark.parametrize(
    ("attempt", "status"),
    [(2, capture.SUBMITTED), (1, capture.RETAKE), (1, capture.REVIEW), (1, capture.CAPTURING)],
)
async def test_a_late_technical_failure_of_a_try_the_step_moved_past_sends_nothing(
    routed: dict[str, list[Any]], attempt: int, status: str
) -> None:
    """A failure that arrives once the step is past the check's try (a newer try, new photos asked
    for, a try held for review) sends nothing again: the try's photos were replaced or judged, and
    a resend would put them on the step's current try."""
    step = routed["step"][0]
    routed["step"][0] = replace(step, state={**step.state, "attempt": attempt, "status": status})
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(attempt=1), "pending", "failed")
    assert done == ["failure_task"]
    assert routed["resend"] == [] and routed["enqueue"] == [] and routed["new_try"] == []
    assert not any("decided_at" in sql for sql in routed["sql"])


@pytest.mark.parametrize("attempt", [1, 3])
async def test_a_restated_rejection_leaves_a_referred_check_with_the_operator(
    routed: dict[str, list[Any]], attempt: int
) -> None:
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(attempt=attempt), "refer", "rejected")
    assert done == []
    assert routed["check"] == ["refer"]
    assert routed["settle"] == []


async def test_a_clear_settles_the_step(routed: dict[str, list[Any]]) -> None:
    session = cast(AsyncSession, routed["session"][0])
    done = await routing.route_result(session, _record(attempt=1), "pending", "clear")
    assert done == ["portal"]
    assert routed["settle"] == ["passed"]


# ── Real Postgres ─────────────────────────────────────────────────────────────────────────────


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _rows(sess: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in (await sess.execute(text(sql), params)).mappings().all()]


async def _seed(sess: AsyncSession, *, step_type: str = "verify_identity") -> dict[str, Any]:
    """A firm, a client with a portal login and a nationality, and one open step of theirs."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"idc_{s}",
            a=f"idc-{s}",
            n=f"Capture {s}",
        )
    )
    platform = str(await _scalar(sess, SECCL_PLATFORM_SQL, n=f"Seccl {s}"))
    contact_uid = uuid.uuid4().hex[:12]
    contact = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, surname, email, "
            "  dob, nationality) "
            "VALUES (:t, :u, 'individual', 'Ivy Doyle', 'Ivy', 'Doyle', :e, '1980-02-03', "
            "  ARRAY['GB']) RETURNING id",
            t=tenant,
            u=contact_uid,
            e=f"ivy_{s}@t.test",
        )
    )
    user = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
            "VALUES (:uid, :t, :e, :w, true) RETURNING id",
            uid=f"cusr_{s}",
            t=tenant,
            e=f"ivy_{s}@t.test",
            w=f"cwos_{s}",
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
            u=f"cflw_{s}",
            t=tenant,
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"cflv_{s}",
            t=tenant,
            f=flow_id,
            d=json.dumps({"steps": [], "milestones": []}),
        )
    )
    execution = int(
        await _scalar(
            sess,
            "INSERT INTO flows.executions "
            "  (uid, tenant_id, flow_id, flow_version_id, crm_application_id, status) "
            "VALUES (:u, :t, :f, :v, :a, 'active') RETURNING id",
            u=uuid.uuid4().hex[:12],
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
    step_key = "idv" if step_type == "verify_identity" else "aml"
    await sess.execute(
        text(
            "INSERT INTO flows.case_steps "
            "  (tenant_id, case_id, step_id, step_key, step_type, title, assignee, status, "
            "   participant_subject_id, data) "
            "VALUES (:t, :c, :k, :k, :st, 'Verify identity', 'client', 'pending', :psid, "
            "        '{}'::jsonb)"
        ),
        {"t": tenant, "c": case, "k": step_key, "st": step_type, "psid": contact},
    )
    return {
        "tenant": tenant,
        "contact": contact,
        "contact_uid": contact_uid,
        "workos": f"cwos_{s}",
        "execution": execution,
        "case": case,
        "case_uid": case_uid,
        "step_key": step_key,
        "nonce": s,
    }


async def _instantiate(
    sess: AsyncSession, fx: dict[str, Any], provider: str, *, step_type: str = "verify_identity"
) -> Any:
    handler = get_handler(step_type)
    assert handler is not None
    ctx = StepContext(
        session=sess,
        tenant_id=fx["tenant"],
        execution_id=fx["execution"],
        case_id=fx["case"],
        step_key=fx["step_key"],
        config={"provider": provider},
        participant_subject_id=fx["contact"],
    )
    event = await handler.instantiate(ctx)
    fx["kyc"] = int(ctx.data["kyc_check_id"])
    return event


def _principal(fx: dict[str, Any]) -> Principal:
    return Principal(user_id=fx["workos"], organisation_id="org_1", role="client", permissions=[])


async def _client(sess: AsyncSession, fx: dict[str, Any]) -> FirmContext:
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
    return FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal(fx))


async def _capture(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    ctx = await _client(sess, fx)
    return await api.my_identity_capture(
        step_key="idv", case_uid=fx["case_uid"], principal=_principal(fx), ctx=ctx
    )


def _items(body: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """``{key: (item uid, status)}`` from the client's read."""
    return {i["key"]: (i["item_uid"], i["status"]) for i in body["items"]}


async def _submit(
    sess: AsyncSession,
    fx: dict[str, Any],
    document_type: str,
    *keys: str,
    files: dict[str, tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """The client's submit, as the portal sends it: the document type and one file per photo.
    The request alone: it holds the photos and queues the send (``_run_submit`` is the worker).

    ``keys`` names the photos sent; without them, each photo the document needs that the capture
    still lists as open, which is what the portal sends. ``files`` replaces a photo's
    ``(bytes, file name)`` (``PHOTOS`` otherwise).
    """
    if not keys:
        listed = _items(await _capture(sess, fx))
        keys = tuple(
            key
            for key in capture.needed_keys(document_type)
            if key in listed and listed[key][1] == "open"
        )
    chosen = {key: (files or {}).get(key, PHOTOS[key]) for key in keys}
    uploads = {
        key: UploadFile(filename=name, file=BytesIO(data)) for key, (data, name) in chosen.items()
    }
    ctx = await _client(sess, fx)
    return await api.submit_my_identity_capture(
        document_type=cast(Any, document_type),
        step_key="idv",
        case_uid=fx["case_uid"],
        idv_document_front=uploads.get(capture.FRONT),
        idv_document_back=uploads.get(capture.BACK),
        idv_selfie=uploads.get(capture.SELFIE),
        principal=_principal(fx),
        ctx=ctx,
    )


def _answered(result: dict[str, Any]) -> dict[str, Any]:
    """The capture a submit answered with: the photos are held and the send is queued."""
    assert isinstance(result, dict)
    assert result["status"] == "submitted"
    return result


async def _queued(sess: AsyncSession, fx: dict[str, Any]) -> list[dict[str, Any]]:
    """Each ``identity.check.requested`` row of the firm, oldest first: ``{status, payload}``."""
    rows = await _rows(
        sess,
        "SELECT status, payload FROM flows.outbox WHERE tenant_id = :t AND event_type = :e "
        "ORDER BY id",
        t=fx["tenant"],
        e=identity.EVENT,
    )
    return [
        {
            "status": r["status"],
            "payload": r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]),
        }
        for r in rows
    ]


async def _held_copies(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, str]:
    """``{item key: slot uid}`` for each photo held for the step."""
    rows = await _rows(
        sess,
        "SELECT item_key, slot_uid FROM compliance.identity_photos "
        "WHERE case_id = :c AND step_key = 'idv' ORDER BY id",
        c=fx["case"],
    )
    return {str(r["item_key"]): str(r["slot_uid"]) for r in rows}


_CLIENT = re.compile(r"^/clients/([^/]+)$")
_DOCUMENT = re.compile(r"^/documents/([^/]+)$")
_DOCUMENT_IMAGE = re.compile(r"^/documents/([^/]+)/(upload|download)/(front|back)$")
_LIVE_PHOTO = re.compile(r"^/livePhotos/([^/]+?)(/download)?$")


def _content_type(data: bytes) -> str:
    try:
        return storage.sniff_document_type(data[:4096])
    except ValueError:
        return "application/octet-stream"


def _not_found(what: str) -> httpx.Response:
    return httpx.Response(404, json={"message": f"{what} not found"})


class FakeComplyCube:
    """ComplyCube's API as the portal calls it, recording each call and holding what it was sent.

    A write answers a new id (``{prefix}_{nonce}_{n}``). A document's images and each live photo
    are kept as they arrived, so the reads answer them: ``GET /documents/{id}`` as
    ``{"id", "images": [{"documentSide", "contentType", "fileName"}]}``, ``GET /livePhotos/{id}``
    as ``{"id", "contentType"}``, and each ``…/download`` as ``{"fileName", "contentType",
    "data"}`` with ``data`` in base64. Anything it does not hold, or holds in ``gone``, answers 404.
    A client made here is kept: ``GET /clients/{id}`` answers it and ``POST /clients/{id}`` merges
    the update into it. Any other read answers an empty listing. ``refuse`` answers that status,
    with ``refusal_message``, to every write whose path starts with ``refuse_path``.
    """

    def __init__(self, nonce: str) -> None:
        self.nonce = nonce
        self.calls: list[str] = []
        #: ``(call, JSON body)`` for each request with a body.
        self.bodies: list[tuple[str, Any]] = []
        self.counter = 0
        self.refuse: int | None = None
        #: The path prefix ``refuse`` applies to: by default every document image upload.
        self.refuse_path = "/documents/"
        self.refusal_message = "unreadable"
        #: Document ids ComplyCube no longer holds.
        self.gone: set[str] = set()
        #: ``{document id: {side: (file name, bytes)}}`` for each document made here.
        self.documents: dict[str, dict[str, tuple[str, bytes]]] = {}
        #: ``{live photo id: bytes}`` for each live photo made here.
        self.live_photos: dict[str, bytes] = {}
        #: The clients created here, by id, as a later fetch answers them.
        self.clients: dict[str, dict[str, Any]] = {}
        #: Client ids ComplyCube does not hold (another key's, or deleted): a read of one, and any
        #: write naming one as its ``clientId``, answers 404.
        self.unknown_clients: set[str] = set()

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.nonce}_{self.counter}"

    def _document(self, document_id: str) -> dict[str, tuple[str, bytes]] | None:
        if document_id in self.gone:
            return None
        return self.documents.get(document_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        call = f"{request.method} {path}"
        self.calls.append(call)
        body = json.loads(request.content) if request.content else None
        if body is not None:
            self.bodies.append((call, body))
        if request.method == "GET":
            if path.removeprefix("/clients/") in self.unknown_clients:
                return _not_found("Client")
            return self._read(path)
        if isinstance(body, dict) and body.get("clientId") in self.unknown_clients:
            return httpx.Response(404, json={"message": f"Invalid client id '{body['clientId']}'"})
        if self.refuse is not None and path.startswith(self.refuse_path):
            return httpx.Response(self.refuse, json={"message": self.refusal_message})
        return self._write(path, body)

    def _read(self, path: str) -> httpx.Response:
        if (found := _CLIENT.match(path)) and found[1] in self.clients:
            return httpx.Response(200, json=self.clients[found[1]])
        if found := _DOCUMENT.match(path):
            images = self._document(found[1])
            if images is None:
                return _not_found("Document")
            listed = [
                {"documentSide": side, "contentType": _content_type(data), "fileName": name}
                for side, (name, data) in images.items()
            ]
            return httpx.Response(200, json={"id": found[1], "images": listed})
        if (found := _DOCUMENT_IMAGE.match(path)) and found[2] == "download":
            image = (self._document(found[1]) or {}).get(found[3])
            if image is None:
                return _not_found("Image")
            name, data = image
            return httpx.Response(200, json=_download(name, data))
        if found := _LIVE_PHOTO.match(path):
            photo = self.live_photos.get(found[1])
            if photo is None:
                return _not_found("Live photo")
            if found[2]:
                ext = identity.FILE_EXTENSIONS.get(_content_type(photo), "jpg")
                return httpx.Response(200, json=_download(f"livePhoto.{ext}", photo))
            return httpx.Response(200, json={"id": found[1], "contentType": _content_type(photo)})
        return httpx.Response(200, json={"items": []})

    def _write(self, path: str, body: Any) -> httpx.Response:
        if (found := _CLIENT.match(path)) and (held := self.clients.get(found[1])) is not None:
            for key, value in (body or {}).items():
                held[key] = {**held.get(key, {}), **value} if isinstance(value, dict) else value
            return httpx.Response(200, json=held)
        if (found := _DOCUMENT_IMAGE.match(path)) and found[2] == "upload":
            images = self._document(found[1])
            if images is None:
                return _not_found("Document")
            images[found[3]] = (str(body["fileName"]), base64.b64decode(body["data"]))
            return httpx.Response(200, json={})
        prefix = {
            "/clients": "cli",
            "/addresses": "adr",
            "/documents": "doc",
            "/livePhotos": "lp",
            "/checks": "chk",
        }.get(path)
        if prefix is None:
            return httpx.Response(200, json={})
        made = self._id(prefix)
        if path == "/clients":
            self.clients[made] = {"id": made, **(body or {})}
        elif path == "/documents":
            self.documents[made] = {}
        elif path == "/livePhotos":
            self.live_photos[made] = base64.b64decode(body["data"])
        return httpx.Response(200, json={"id": made, "status": "pending"})

    def file_names(self) -> dict[str, str]:
        """``{side: fileName}`` for each document image uploaded."""
        return {
            call.rsplit("/", 1)[-1]: str(body["fileName"])
            for call, body in self.bodies
            if "/upload/" in call
        }

    def posts(self, since: int = 0) -> list[str]:
        """Each write from call ``since`` on."""
        return [call for call in self.calls[since:] if call.startswith("POST")]

    def client(self) -> ScreeningClient:
        async def no_sleep(seconds: float) -> None:
            del seconds

        return ScreeningClient(
            "test-key", transport=httpx.MockTransport(self.handler), sleep=no_sleep, max_retries=0
        )


def _download(name: str, data: bytes) -> dict[str, str]:
    return {
        "fileName": name,
        "contentType": _content_type(data),
        "data": base64.b64encode(data).decode("ascii"),
    }


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """ComplyCube configured, and a master key to seal the client's photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    yield


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """An in-memory firm bucket: anything written to object storage lands here."""
    stored: dict[str, bytes] = {}

    async def document_bucket(_session: AsyncSession, _tenant_id: int | None = None) -> str:
        return "documents-test"

    async def put(key: str, data: bytes, _ct: str, *, bucket: str | None = None) -> None:
        del bucket
        stored[key] = data

    async def get(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        del bucket
        return stored[key], "image/jpeg"

    monkeypatch.setattr(tenant_storage, "document_bucket", document_bucket)
    monkeypatch.setattr(storage, "put_document", put)
    monkeypatch.setattr(storage, "get_document", get)
    return stored


def _use(fake: FakeComplyCube, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the worker's send, and every read of a check's images, at ``fake``."""
    monkeypatch.setattr(identity, "require_client", fake.client)
    monkeypatch.setattr(decisions, "require_client", fake.client)


async def _worker(sess: AsyncSession, fx: dict[str, Any]) -> None:
    """The outbox dispatcher's turn on the oldest queued ``identity.check.requested`` row: it is
    delivered when ``identity.submit`` returns, and stays queued for a retry when it raises."""
    (row,) = await _rows(
        sess,
        "SELECT id, payload FROM flows.outbox WHERE tenant_id = :t AND event_type = :e "
        "  AND status = 'queued' ORDER BY id LIMIT 1",
        t=fx["tenant"],
        e=identity.EVENT,
    )
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    await identity.submit(sess, fx["tenant"], payload)
    await sess.execute(
        text("UPDATE flows.outbox SET status = 'delivered', delivered_at = now() WHERE id = :id"),
        {"id": row["id"]},
    )


async def _run_submit(
    sess: AsyncSession, fx: dict[str, Any], fake: FakeComplyCube, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker's send of the queued submit, to ``fake``."""
    _use(fake, monkeypatch)
    await _worker(sess, fx)


async def _try(
    sess: AsyncSession,
    fx: dict[str, Any],
    fake: FakeComplyCube,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str = "driving_licence",
    *,
    keys: tuple[str, ...] = (),
    files: dict[str, tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """One whole try: the client's submit (``keys`` and ``files`` as ``_submit`` takes them) and
    the worker's send to ``fake``. Answers the capture as the client then reads it."""
    _use(fake, monkeypatch)
    _answered(await _submit(sess, fx, document_type, *keys, files=files))
    await _worker(sess, fx)
    return await _capture(sess, fx)


async def _latest_check(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    rows = await _rows(
        sess,
        "SELECT id, uid, status, attempt, origin, kind, decided_at, decided_by, "
        "  decision_comment, document_id, provider_document_id, provider_live_photo_id "
        "FROM compliance.checks WHERE kyc_check_id = :k ORDER BY id DESC LIMIT 1",
        k=fx["kyc"],
    )
    return rows[0]


async def _checks(sess: AsyncSession, fx: dict[str, Any]) -> int:
    """How many checks the step's tries started."""
    return int(
        await _scalar(
            sess, "SELECT count(*) FROM compliance.checks WHERE kyc_check_id = :k", k=fx["kyc"]
        )
    )


async def _result(
    sess: AsyncSession, fx: dict[str, Any], new: str, *, result: dict[str, Any] | None = None
) -> list[str]:
    """ComplyCube's result for the newest check, routed as the consumer routes it."""
    check = await _latest_check(sess, fx)
    await sess.execute(
        text(
            "UPDATE compliance.checks SET status = :st, result = CAST(:r AS jsonb) WHERE id = :id"
        ),
        {"st": new, "r": json.dumps(result or {"outcome": new}), "id": check["id"]},
    )
    record = await routing.load_check(sess, int(check["id"]))
    assert record is not None
    return await routing.route_result(sess, record, str(check["status"]), new)


async def _decide(sess: AsyncSession, fx: dict[str, Any], verdict: str) -> list[str]:
    """An operator's verdict on the newest check, as the AML console records it."""
    check = await _latest_check(sess, fx)
    await sess.execute(
        text(
            "UPDATE compliance.checks SET status = :st, decided_by = 'Operator', "
            "  decided_at = now(), decision_comment = 'Reviewed' WHERE id = :id"
        ),
        {"st": verdict, "id": check["id"]},
    )
    record = await routing.load_check(sess, int(check["id"]))
    assert record is not None
    return await routing.route_result(sess, record, str(check["status"]), verdict)


async def _step(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    rows = await _rows(
        sess,
        "SELECT cs.status, cs.data ->> 'last_event' AS last_event, k.status AS kyc "
        "FROM flows.case_steps cs "
        "JOIN crm.kyc_checks k ON k.id = CAST(cs.data ->> 'kyc_check_id' AS bigint) "
        "WHERE cs.case_id = :c AND cs.step_key = 'idv'",
        c=fx["case"],
    )
    return rows[0]


async def _stored_capture(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    """The capture as the step stores it, ops-only fields included."""
    data = await _scalar(
        sess,
        "SELECT data FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'",
        c=fx["case"],
    )
    state = capture.state_of(data)
    assert state is not None
    return state


async def _documents_kept(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, int]:
    """What the firm's document store holds for the tenant: files and request items."""
    return {
        "documents": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.documents WHERE tenant_id = :t",
                t=fx["tenant"],
            )
        ),
        "request_items": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.request_items WHERE tenant_id = :t",
                t=fx["tenant"],
            )
        ),
    }


@pytest.mark.asyncio
async def test_a_retired_idenfy_step_asks_for_photos_on_complycube(
    service_session: AsyncSession, complycube: None
) -> None:
    sess = service_session
    fx = await _seed(sess)
    event = await _instantiate(sess, fx, "idenfy")
    assert event.signal == StepSignal.WAITING
    assert await _scalar(
        sess, "SELECT provider FROM crm.kyc_checks WHERE id = :k", k=fx["kyc"]
    ) == ("complycube")
    body = await _capture(sess, fx)
    assert [item["key"] for item in body["items"]] == list(capture.KEYS)
    # Slots on the step: an open photo with its uid, and nothing of a document request.
    for item in body["items"]:
        assert set(item) == {"key", "item_uid", "status"}
        assert item["status"] == "open" and item["item_uid"]
    assert body["status"] == "capturing"
    assert body["attempt"] == 1
    assert "max_attempts" not in body
    assert body["last_reason"] is None
    assert body["document_type"] is None
    assert body["manual_offered"] is True
    assert await _documents_kept(sess, fx) == {"documents": 0, "request_items": 0}
    assert await _held_copies(sess, fx) == {}
    # The photos are evidence, not the step's artifact: nothing links one as the step's own item.
    assert (
        await _scalar(
            sess,
            "SELECT request_item_id FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'",
            c=fx["case"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_the_photos_are_the_clients_to_take_whoever_the_step_was_assigned_to(
    service_session: AsyncSession, complycube: None
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await sess.execute(
        text("UPDATE flows.case_steps SET assignee = 'staff' WHERE case_id = :c"),
        {"c": fx["case"]},
    )
    await _instantiate(sess, fx, "complycube")
    assert (
        await _scalar(
            sess,
            "SELECT assignee FROM flows.case_steps WHERE case_id = :c AND step_key = 'idv'",
            c=fx["case"],
        )
        == "client"
    )


@pytest.mark.asyncio
async def test_without_complycube_no_photos_are_asked_for(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    with pytest.raises(HTTPException) as exc:
        await _capture(sess, fx)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_the_submit_holds_the_photos_sealed_and_queues_the_send(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    before = _items(await _capture(sess, fx))
    assert {status for _uid, status in before.values()} == {"open"}
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)

    body = _answered(await _submit(sess, fx, "driving_licence"))

    # Submitted at once: the client waits while the worker sends the photos.
    assert body["attempt"] == 1
    assert body["document_type"] == "driving_licence"
    assert body["last_reason"] is None
    assert (await _capture(sess, fx))["status"] == "submitted"
    # Nothing reaches ComplyCube in the request, and no check is started yet.
    assert fake.calls == []
    assert await _checks(sess, fx) == 0
    # One send is queued, naming the try's slots.
    slots = {key: uid for key, (uid, _s) in before.items()}
    assert await _queued(sess, fx) == [
        {
            "status": "queued",
            "payload": {
                "contact_id": fx["contact"],
                "case_id": fx["case"],
                "step_key": "idv",
                "kyc_check_id": fx["kyc"],
                "attempt": 1,
                "document_type": "driving_licence",
                "items": slots,
            },
        }
    ]
    # Each photo is held sealed under its slot: unreadable as stored, the photo once unsealed.
    held = await _rows(
        sess,
        "SELECT tenant_id, contact_id, slot_uid, item_key, content_type, sealed "
        "FROM compliance.identity_photos WHERE case_id = :c ORDER BY id",
        c=fx["case"],
    )
    assert [(r["item_key"], r["slot_uid"]) for r in held] == list(slots.items())
    for row in held:
        plain = PHOTOS[str(row["item_key"])][0]
        sealed = bytes(row["sealed"])
        assert row["tenant_id"] == fx["tenant"] and row["contact_id"] == fx["contact"]
        assert row["content_type"] == "image/jpeg"
        assert sealed != plain and plain[:64] not in sealed
        assert crypto.unseal(sealed) == plain
    # Nothing in object storage, and nothing of the photos in the firm's document store.
    assert bucket == {}
    assert await _documents_kept(sess, fx) == {"documents": 0, "request_items": 0}

    # Sent once: a second submit, even before the worker runs, is refused.
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "driving_licence", *capture.KEYS)
    assert exc.value.status_code == 409
    assert exc.value.detail == "These photos have already been sent"
    assert len(await _queued(sess, fx)) == 1


@pytest.mark.asyncio
async def test_the_worker_sends_the_held_photos_and_starts_the_check(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    before = _items(await _capture(sess, fx))
    fake = FakeComplyCube(fx["nonce"])

    body = await _try(sess, fx, fake, monkeypatch)

    assert body["status"] == "submitted"
    assert body["attempt"] == 1
    assert body["document_type"] == "driving_licence"
    assert _items(body) == {key: (uid, "fulfilled") for key, (uid, _s) in before.items()}
    assert [row["status"] for row in await _queued(sess, fx)] == ["delivered"]
    check = await _latest_check(sess, fx)
    document_id = str(check["provider_document_id"])
    live_photo_id = str(check["provider_live_photo_id"])
    assert fake.posts() == [
        "POST /clients",
        "POST /documents",
        f"POST /documents/{document_id}/upload/front",
        f"POST /documents/{document_id}/upload/back",
        "POST /livePhotos",
        "POST /checks",
    ]
    # Each photo reached ComplyCube as the client sent it, named for what it is.
    assert fake.documents == {
        document_id: {"front": ("front.jpg", FRONT_JPEG), "back": ("back.jpg", BACK_JPEG)}
    }
    assert fake.live_photos == {live_photo_id: SELFIE_JPEG}
    made = next(b for call, b in fake.bodies if call == "POST /documents")
    assert made["type"] == "driving_license"
    assert made["issuingCountry"] == "GB"
    started = next(b for call, b in fake.bodies if call == "POST /checks")
    assert started["type"] == "identity_check"
    assert started["documentId"] == document_id
    assert started["livePhotoId"] == live_photo_id
    assert check["origin"] == "portal"
    assert check["kind"] == "identity"
    assert check["status"] == "pending"
    assert check["attempt"] == 1
    assert check["document_id"] is not None

    # Our rows hold ComplyCube's ids only: no object of ours behind any of them.
    document = (
        await _rows(
            sess,
            "SELECT object_key, back_object_key, document_type, provider_document_id "
            "FROM compliance.documents WHERE id = :d",
            d=check["document_id"],
        )
    )[0]
    assert document == {
        "object_key": None,
        "back_object_key": None,
        "document_type": "driving_license",
        "provider_document_id": document_id,
    }
    photo = await _rows(
        sess,
        "SELECT object_key FROM compliance.live_photos WHERE provider_photo_id = :p",
        p=live_photo_id,
    )
    assert photo == [{"object_key": None}]
    assert bucket == {}
    assert await _documents_kept(sess, fx) == {"documents": 0, "request_items": 0}

    # What ComplyCube holds is kept for a later send, and is never the client's to read.
    assert (await _stored_capture(sess, fx))[capture.PROGRESS_KEY] == {
        "accepted": {key: uid for key, (uid, _s) in before.items()},
        "document_id": document_id,
        "document_type": "driving_licence",
        "live_photo_id": live_photo_id,
    }
    read = json.dumps(await _capture(sess, fx))
    for held in (capture.PROGRESS_KEY, document_id, live_photo_id):
        assert held not in read
    # Our sealed copies stay until the identity step finishes.
    assert await _held_copies(sess, fx) == {key: uid for key, (uid, _s) in before.items()}
    assert (await _step(sess, fx))["kyc"] == "pending"


@pytest.mark.asyncio
async def test_a_passport_needs_no_back(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    # The front and the face are all a passport needs: no back is asked for, held or sent.
    body = await _try(sess, fx, fake, monkeypatch, "passport", keys=(capture.FRONT, capture.SELFIE))
    assert body["status"] == "submitted"
    assert body["document_type"] == "passport"
    assert [item["key"] for item in body["items"]] == [capture.FRONT, capture.SELFIE]
    assert set(await _held_copies(sess, fx)) == {capture.FRONT, capture.SELFIE}
    assert not any(call.endswith("/upload/back") for call in fake.calls)
    check = await _latest_check(sess, fx)
    assert fake.documents == {
        str(check["provider_document_id"]): {"front": ("front.jpg", FRONT_JPEG)}
    }
    made = next(b for call, b in fake.bodies if call == "POST /documents")
    assert made["type"] == "passport"
    assert bucket == {}


@pytest.mark.asyncio
async def test_a_document_side_may_be_a_pdf_and_is_sent_as_one(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    # Each file is named for what its bytes are, never for the name the client's device gave it.
    files = {
        capture.FRONT: (PDF, "licence-scan.pdf"),
        capture.BACK: (BACK_PNG, "IMG_0001.jpeg"),
        capture.SELFIE: (SELFIE_PNG, "selfie.png"),
    }
    body = await _try(sess, fx, fake, monkeypatch, files=files)
    assert body["status"] == "submitted"
    held = await _rows(
        sess,
        "SELECT item_key, content_type FROM compliance.identity_photos WHERE case_id = :c",
        c=fx["case"],
    )
    assert {r["item_key"]: r["content_type"] for r in held} == {
        capture.FRONT: "application/pdf",
        capture.BACK: "image/png",
        capture.SELFIE: "image/png",
    }
    assert fake.file_names() == {"front": "front.pdf", "back": "back.png"}
    check = await _latest_check(sess, fx)
    assert fake.documents[str(check["provider_document_id"])] == {
        "front": ("front.pdf", PDF),
        "back": ("back.png", BACK_PNG),
    }
    assert fake.live_photos == {str(check["provider_live_photo_id"]): SELFIE_PNG}
    assert bucket == {}


#: A Word document: a zip, which no photo may be.
def _docx() -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "x" * PADDING)
    return out.getvalue()


_FACE = "The photo of your face must be a JPG or PNG."
_DOCUMENT_TYPE = "Upload your document as a JPG, PNG or PDF."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "kind", "told"),
    [
        (capture.SELFIE, "pdf", _FACE),
        (capture.SELFIE, "gif", _FACE),
        (capture.FRONT, "text", _DOCUMENT_TYPE),
        (capture.BACK, "docx", _DOCUMENT_TYPE),
        (
            capture.FRONT,
            "small",
            "The photo of your document's front must be between 34 KB and 4 MB.",
        ),
        (capture.SELFIE, "large", "The photo of your face must be between 34 KB and 4 MB."),
        (
            capture.BACK,
            "empty",
            "The photo of your document's back must be between 34 KB and 4 MB.",
        ),
    ],
)
async def test_a_photo_complycube_would_not_take_is_refused_and_nothing_is_held(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    kind: str,
    told: str,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)
    data = {
        "pdf": lambda: PDF,
        "gif": lambda: GIF,
        "text": lambda: b"my passport number is 123\n" * 2000,
        "docx": _docx,
        "small": lambda: FRONT_JPEG[: capture.MIN_PHOTO_BYTES - 1],
        "large": lambda: b"\xff\xd8\xff\xe0" + b"\x00" * capture.MAX_PHOTO_BYTES,
        "empty": lambda: b"",
    }[kind]()
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "driving_licence", files={key: (data, f"{kind}.upload")})
    assert exc.value.status_code == 422
    assert exc.value.detail == told
    # Refused in the request: nothing held, nothing queued, nothing sent, nothing moved on.
    assert await _held_copies(sess, fx) == {}
    assert await _queued(sess, fx) == []
    assert fake.calls == []
    assert bucket == {}
    body = await _capture(sess, fx)
    assert body["status"] == "capturing"
    assert {status for _uid, status in _items(body).values()} == {"open"}


@pytest.mark.asyncio
async def test_a_second_submit_is_refused_rather_than_paying_for_a_second_check(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "passport")
    sent = len(fake.calls)
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "passport", capture.FRONT, capture.SELFIE)
    assert exc.value.status_code == 409
    assert exc.value.detail == "These photos have already been sent"
    assert len(fake.calls) == sent
    assert len(await _queued(sess, fx)) == 1
    assert await _checks(sess, fx) == 1


@pytest.mark.asyncio
async def test_a_licence_is_not_sent_without_its_back(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "driving_licence", capture.FRONT, capture.SELFIE)
    assert exc.value.status_code == 409
    assert exc.value.detail == "Send every photo before you start the check"
    assert await _held_copies(sess, fx) == {}
    assert await _queued(sess, fx) == []
    assert (await _capture(sess, fx))["status"] == "capturing"


@pytest.mark.asyncio
async def test_a_client_with_no_nationality_is_told_what_to_do(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComplyCube needs the document's issuing country, read from the nationality or else the
    address country. Without either the check cannot start, and the client is told in a sentence
    they can act on. The client portal shows this detail as it stands
    (apps/client/src/routes/onboarding/identity-actions.test.ts)."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    await sess.execute(
        text("UPDATE crm.contacts SET nationality = '{}', address = NULL WHERE id = :c"),
        {"c": fx["contact"]},
    )
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "passport")
    assert exc.value.status_code == 409
    assert exc.value.detail == api.NATIONALITY_NEEDED
    assert exc.value.detail == (
        "We need your nationality before we can check your ID. Please ask your adviser to add it."
    )
    assert await _held_copies(sess, fx) == {}
    assert await _queued(sess, fx) == []
    assert (await _capture(sess, fx))["status"] == "capturing"


@pytest.mark.asyncio
async def test_a_send_that_cannot_start_for_the_client_is_not_tried_again(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nationality is gone by the time the worker sends: sending again will not help, so the
    row fails for good rather than retrying, and ComplyCube is never called."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)
    _answered(await _submit(sess, fx, "passport"))
    await sess.execute(
        text("UPDATE crm.contacts SET nationality = '{}', address = NULL WHERE id = :c"),
        {"c": fx["contact"]},
    )
    with pytest.raises(PermanentDispatchError):
        await _worker(sess, fx)
    assert fake.calls == []
    assert await _checks(sess, fx) == 0


@pytest.mark.asyncio
async def test_photos_that_cannot_be_sealed_are_not_taken(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the master key nothing can be held, so nothing is taken: the client is told to
    send again, and nothing is queued."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    monkeypatch.setattr(settings, "secrets_master_key", "")
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "passport")
    assert exc.value.status_code == 503
    assert exc.value.detail == api.SEND_AGAIN
    assert await _held_copies(sess, fx) == {}
    assert await _queued(sess, fx) == []
    assert (await _capture(sess, fx))["status"] == "capturing"
    assert bucket == {}


async def _console(
    sess: AsyncSession, fx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> aml_router.AmlContext:
    """An operator in the AML console, on the test's own session."""
    del monkeypatch
    if "console" in fx:
        return cast(aml_router.AmlContext, fx["console"])
    nonce = fx["nonce"]
    user = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
            "VALUES (:uid, :t, :e, :w, true) RETURNING id",
            uid=f"cops_{nonce}",
            t=fx["tenant"],
            e=f"ops_{nonce}@t.test",
            w=f"wos_ops_{nonce}",
        )
    )
    ctx = aml_router.AmlContext(
        session=sess,
        tenant_id=fx["tenant"],
        targeted=True,
        foreign=False,
        principal=Principal(
            user_id=f"wos_ops_{nonce}",
            organisation_id="org_ops",
            role="member",
            permissions=["aml:read", "aml:write", "aml:adjudicate"],
        ),
        user_id=user,
        own_tenant_id=fx["tenant"],
    )
    fx["console"] = ctx
    return ctx


async def _ask(
    sess: AsyncSession,
    fx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    photos: list[str],
    reason: str = "none",
    comment: str = "Checked the images against the document.",
) -> dict[str, Any]:
    """An operator's "Ask for new photos" on the newest check."""
    ctx = await _console(sess, fx, monkeypatch)
    check = await _latest_check(sess, fx)
    return await aml_router.request_photos(
        str(check["uid"]),
        aml_router.PhotoRequest(
            photos=cast(Any, photos), reason=cast(Any, reason), comment=comment
        ),
        ctx=ctx,
    )


async def _operations(
    sess: AsyncSession, fx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> int:
    """Graphene's operations tenant, with the compliance role its screening tasks go to."""
    ops = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, 'Ops') RETURNING id",
            u=f"opt_{fx['nonce']}",
            a=f"opt-{fx['nonce']}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
            "VALUES (:t, :s, 'Compliance', 'operations')"
        ),
        {"t": ops, "s": tasks.COMPLIANCE_ROLE_SLUG},
    )

    async def ops_tenant(_session: AsyncSession) -> int:
        return ops

    monkeypatch.setattr(tasks, "operations_tenant_id", ops_tenant)
    return ops


async def _console_row(
    sess: AsyncSession, fx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The client's row in the console's client list."""
    ctx = await _console(sess, fx, monkeypatch)
    listed = await aml_router.list_clients(ctx=ctx, contact=fx["contact_uid"])
    (row,) = cast(list[dict[str, Any]], listed["items"])
    return row


#: ComplyCube's ``attention`` on a real identity check: the face did not match, liveness passed.
FACE_MISMATCH = {
    "outcome": "attention",
    "breakdown": {
        "faceAnalysis": {"facialSimilarity": "attention"},
        "facialSimilarityScore": 0,
        "authenticityAnalysis": {"livenessCheck": "clear"},
        "livenessCheckScore": 83,
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "result"),
    [("rejected", {"outcome": "rejected"}), ("refer", FACE_MISMATCH)],
)
async def test_a_try_complycube_does_not_clear_goes_to_an_operator_on_the_first_try(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
    result: dict[str, Any],
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, FakeComplyCube(fx["nonce"]), monkeypatch, "passport")
    before = _items(await _capture(sess, fx))
    assert await _result(sess, fx, verdict, result=result) == ["portal_review"]

    body = await _capture(sess, fx)
    # The client sees "a person is checking": no new try, nothing to retake, and no reason.
    assert body["status"] == "review"
    assert body["attempt"] == 1
    assert body["last_reason"] is None
    assert _items(body) == before
    for word in ("facialSimilarity", "attention", "liveness", "rejected"):
        assert word not in json.dumps(body)
    held = await _latest_check(sess, fx)
    assert held["status"] == "refer"
    assert held["decided_at"] is None and held["decided_by"] is None
    row = await _console_row(sess, fx, monkeypatch)
    assert row["identity"]["uid"] == held["uid"]
    assert row["identity"]["decided"] is False
    assert row["needs_attention"] is True
    step = await _step(sess, fx)
    assert step["kyc"] == "pending"
    assert step["status"] in ("pending", "waiting")
    # The firm sees the step with Graphene.
    firm = _staff(CASES_READ.slug)
    assert (await _journey_step(sess, fx, firm))["identity_review"] is True
    # Under review the step is not finished: our copies stay.
    assert set(await _held_copies(sess, fx)) == {capture.FRONT, capture.SELFIE}

    # ComplyCube restating a verdict decides nothing.
    assert await _result(sess, fx, "rejected") == []
    assert (await _latest_check(sess, fx))["status"] == "refer"
    assert (await _step(sess, fx))["kyc"] == "pending"
    assert (await _capture(sess, fx))["status"] == "review"


@pytest.mark.asyncio
async def test_tries_go_on_until_an_operator_clears_it(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """submit → rejected → an operator asks for new photos, again and again (there is no try
    limit) → an operator clears it, and our copies go."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])

    await _try(sess, fx, fake, monkeypatch)
    first_items = {i["item_uid"] for i in (await _capture(sess, fx))["items"]}
    assert await _result(sess, fx, "rejected") == ["portal_review"]
    await _ask(sess, fx, monkeypatch, ["document", "selfie"], "blurry")
    body = await _capture(sess, fx)
    assert body["status"] == "retake"
    assert body["attempt"] == 2
    assert body["last_reason"] == identity.PHOTO_REQUEST_REASONS["blurry"]
    assert {i["status"] for i in body["items"]} == {"open"}
    assert not first_items & {i["item_uid"] for i in body["items"]}
    assert await _held_copies(sess, fx) == {}
    assert (await _step(sess, fx))["kyc"] == "pending"

    await _try(sess, fx, fake, monkeypatch, "passport")
    assert (await _latest_check(sess, fx))["attempt"] == 2
    assert await _result(sess, fx, "rejected") == ["portal_review"]
    await _ask(sess, fx, monkeypatch, ["document", "selfie"])
    body = await _capture(sess, fx)
    assert body["attempt"] == 3
    assert body["last_reason"] is None

    await _try(sess, fx, fake, monkeypatch, "national_id")
    assert (await _latest_check(sess, fx))["attempt"] == 3
    assert await _result(sess, fx, "rejected") == ["portal_review"]
    body = await _capture(sess, fx)
    assert body["status"] == "review"
    assert body["attempt"] == 3
    held = await _latest_check(sess, fx)
    assert held["status"] == "refer"
    # No try limit: an operator can still ask for new photos on the third try.
    await _ask(sess, fx, monkeypatch, ["selfie"])
    body = await _capture(sess, fx)
    assert body["attempt"] == 4
    assert {i["key"] for i in body["items"] if i["status"] == "open"} == {capture.SELFIE}
    await _try(sess, fx, fake, monkeypatch, "national_id")
    assert (await _latest_check(sess, fx))["attempt"] == 4
    assert await _result(sess, fx, "rejected") == ["portal_review"]

    # ComplyCube restating its rejection does not decide a check an operator holds.
    assert await _result(sess, fx, "rejected") == []
    assert (await _latest_check(sess, fx))["status"] == "refer"
    assert (await _step(sess, fx))["kyc"] == "pending"
    assert set(await _held_copies(sess, fx)) == set(capture.KEYS)

    await _decide(sess, fx, "clear")
    step = await _step(sess, fx)
    assert step["kyc"] == "passed"
    assert step["status"] == "fulfilled"
    assert step["last_event"] == "identity_verified"
    # The step has finished: nothing of the client's photos is held any more.
    assert await _held_copies(sess, fx) == {}
    assert bucket == {}


@pytest.mark.asyncio
async def test_an_operator_rejecting_a_held_try_fails_the_step_and_drops_the_photos(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, FakeComplyCube(fx["nonce"]), monkeypatch, "passport")
    await _result(sess, fx, "rejected")
    assert (await _capture(sess, fx))["status"] == "review"
    assert await _held_copies(sess, fx) != {}
    await _decide(sess, fx, "rejected")
    step = await _step(sess, fx)
    assert step["kyc"] == "failed"
    assert step["status"] == "failed"
    assert step["last_event"] == "identity_failed"
    assert await _held_copies(sess, fx) == {}


@pytest.mark.asyncio
async def test_a_clear_result_passes_the_step_and_drops_the_photos(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, FakeComplyCube(fx["nonce"]), monkeypatch, "passport")
    assert set(await _held_copies(sess, fx)) == {capture.FRONT, capture.SELFIE}
    assert await _result(sess, fx, "clear") == ["portal"]
    step = await _step(sess, fx)
    assert step["kyc"] == "passed"
    assert step["status"] == "fulfilled"
    assert await _held_copies(sess, fx) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("photos", "reason"),
    [(["selfie"], "face"), (["document"], "cropped"), (["document", "selfie"], "none")],
)
async def test_asking_for_new_photos_reopens_only_those_chosen(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    photos: list[str],
    reason: str,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    sent = _items(await _capture(sess, fx))
    await _result(sess, fx, "refer", result=FACE_MISMATCH)
    held = await _latest_check(sess, fx)

    detail = await _ask(sess, fx, monkeypatch, photos, reason, comment="Selfie is dark")

    body = await _capture(sess, fx)
    assert body["status"] == "retake"
    assert body["attempt"] == 2
    assert body["last_reason"] == identity.PHOTO_REQUEST_REASONS[cast(Any, reason)]
    reopened = {key for photo in photos for key in capture.PHOTO_ITEMS[photo]}
    now = _items(body)
    for key in capture.KEYS:
        if key in reopened:
            assert now[key][1] == "open"
            assert now[key][0] != sent[key][0]
        else:
            assert now[key] == (sent[key][0], "fulfilled")
    # A try that keeps a photo keeps the document type it was of.
    assert body["document_type"] == (None if reopened == set(capture.KEYS) else "driving_licence")
    # It keeps what ComplyCube holds of the kept photos, and nothing of a reopened one.
    kept = (await _stored_capture(sess, fx)).get(capture.PROGRESS_KEY)
    expected: dict[str, Any] = {"accepted": {}}
    if capture.FRONT not in reopened:
        expected["accepted"].update({k: sent[k][0] for k in (capture.FRONT, capture.BACK)})
        expected["document_id"] = held["provider_document_id"]
        expected["document_type"] = "driving_licence"
    if capture.SELFIE not in reopened:
        expected["accepted"][capture.SELFIE] = sent[capture.SELFIE][0]
        expected["live_photo_id"] = held["provider_live_photo_id"]
    assert kept == (expected if expected["accepted"] else None)
    # Our copy of a reopened photo goes; the kept photos' copies stay.
    assert await _held_copies(sess, fx) == {
        key: sent[key][0] for key in capture.KEYS if key not in reopened
    }
    # The operator's note is the record's, never the client's.
    assert "Selfie is dark" not in json.dumps(body)

    # Decided under the operator, still `refer`, needing nobody; nothing at ComplyCube.
    check = cast(dict[str, Any], detail["check"])
    assert check["status"] == "refer"
    assert check["decided_at"] is not None
    assert check["decided_by"] == f"ops_{fx['nonce']}@t.test"
    assert check["decision_comment"] == "Selfie is dark"
    # The console lists the images ComplyCube holds for the check, read from ComplyCube.
    identity_block = cast(dict[str, Any], detail["identity"])
    assert [image["part"] for image in identity_block["images"]] == ["front", "back", "selfie"]
    row = await _console_row(sess, fx, monkeypatch)
    assert row["identity"]["uid"] == held["uid"]
    assert row["identity"]["decided"] is True
    assert row["needs_attention"] is False
    assert not any("validate" in call for call in fake.calls)
    step = await _step(sess, fx)
    assert step["kyc"] == "pending"
    assert step["status"] in ("pending", "waiting")
    # The step is the client's again.
    firm = _staff(CASES_READ.slug)
    assert (await _journey_step(sess, fx, firm))["identity_review"] is False

    # Decided now: it cannot be asked again or decided.
    with pytest.raises(HTTPException) as exc:
        await _ask(sess, fx, monkeypatch, photos, reason)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_asking_for_new_photos_closes_the_checks_task_and_needs_a_note(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    ops = await _operations(sess, fx, monkeypatch)
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, FakeComplyCube(fx["nonce"]), monkeypatch, "passport")
    await _result(sess, fx, "rejected")
    held = await _latest_check(sess, fx)
    await tasks.raise_screening_task(
        sess,
        contact_id=fx["contact"],
        tenant_id=fx["tenant"],
        title="Review the identity check for Ivy Doyle",
        description="Review it in the AML console.",
        source_ref=tasks.check_task_ref(str(held["uid"])),
    )

    with pytest.raises(HTTPException) as exc:
        await _ask(sess, fx, monkeypatch, ["selfie"], "face", comment="   ")
    assert exc.value.status_code == 422
    assert (await _latest_check(sess, fx))["decided_at"] is None

    await _ask(sess, fx, monkeypatch, ["selfie"], "face")
    raised = await _rows(sess, "SELECT status FROM tasks.tasks WHERE tenant_id = :t", t=ops)
    assert [row["status"] for row in raised] == ["done"]


@pytest.mark.asyncio
async def test_a_selfie_retake_sends_the_selfie_alone_and_reuses_the_document(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    sent = _items(await _capture(sess, fx))
    first = await _latest_check(sess, fx)
    document_id = str(first["provider_document_id"])
    await _result(sess, fx, "refer", result=FACE_MISMATCH)
    await _ask(sess, fx, monkeypatch, ["selfie"], "face")

    # What ComplyCube holds of the kept document is the worker's, never the client's.
    progress = (await _stored_capture(sess, fx))[capture.PROGRESS_KEY]
    assert progress == {
        "document_id": document_id,
        "document_type": "driving_licence",
        "accepted": {capture.FRONT: sent[capture.FRONT][0], capture.BACK: sent[capture.BACK][0]},
    }
    assert document_id not in json.dumps(await _capture(sess, fx))

    # The kept photos are of a licence: the retake cannot become a passport.
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, "passport")
    assert exc.value.status_code == 409
    assert exc.value.detail == api.SAME_DOCUMENT_NEEDED

    before = len(fake.calls)
    # The client sends only the selfie: the sides ComplyCube holds are not asked for.
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))
    assert body["status"] == "submitted"
    resubmit = fake.calls[before:]
    # The same ComplyCube document, nothing uploaded to it again: a new live photo and check.
    assert f"GET /documents/{document_id}" in resubmit
    assert fake.posts(before) == ["POST /livePhotos", "POST /checks"]
    assert list(fake.documents) == [document_id]
    check = await _latest_check(sess, fx)
    assert check["provider_document_id"] == document_id
    assert check["provider_live_photo_id"] != first["provider_live_photo_id"]
    assert check["attempt"] == 2
    assert bucket == {}


@pytest.mark.asyncio
async def test_a_document_retake_makes_a_new_document_and_reuses_the_live_photo(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    sent = _items(await _capture(sess, fx))
    first = await _latest_check(sess, fx)
    old = str(first["provider_document_id"])
    live_photo_id = str(first["provider_live_photo_id"])
    await _result(sess, fx, "rejected")
    await _ask(sess, fx, monkeypatch, ["document"], "expired")

    # The old document's images were judged: only the live photo is kept for the next send.
    assert (await _stored_capture(sess, fx))[capture.PROGRESS_KEY] == {
        "accepted": {capture.SELFIE: sent[capture.SELFIE][0]},
        "live_photo_id": live_photo_id,
    }
    assert await _held_copies(sess, fx) == {capture.SELFIE: sent[capture.SELFIE][0]}
    body = await _capture(sess, fx)
    assert body["last_reason"] == identity.PHOTO_REQUEST_REASONS["expired"]
    assert _items(body)[capture.SELFIE] == (sent[capture.SELFIE][0], "fulfilled")

    # The client sends a passport this time: only its photo page.
    before = len(fake.calls)
    await _try(sess, fx, fake, monkeypatch, "passport", keys=(capture.FRONT,))
    check = await _latest_check(sess, fx)
    new = str(check["provider_document_id"])
    assert new != old
    assert fake.posts(before) == [
        "POST /documents",
        f"POST /documents/{new}/upload/front",
        "POST /checks",
    ]
    made = [b for call, b in fake.bodies if call == "POST /documents"]
    assert made[-1]["type"] == "passport"
    started = [b for call, b in fake.bodies if call == "POST /checks"]
    assert started[-1]["documentId"] == new
    assert started[-1]["livePhotoId"] == live_photo_id
    assert check["provider_live_photo_id"] == live_photo_id
    assert check["attempt"] == 2
    assert len(fake.live_photos) == 1


@pytest.mark.asyncio
async def test_a_technical_failure_sends_the_same_photos_again_once_and_asks_the_client_nothing(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    ops = await _operations(sess, fx, monkeypatch)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "passport")
    sent = _items(await _capture(sess, fx))
    copies = await _held_copies(sess, fx)
    failed = await _latest_check(sess, fx)
    assert await _result(sess, fx, "failed") == ["photos_resent"]

    # The client is asked for nothing: the same slots, all still held, at the same try, waiting.
    body = await _capture(sess, fx)
    assert body["status"] == "submitted"
    assert body["attempt"] == 1
    assert body["last_reason"] is None
    assert body["document_type"] == "passport"
    assert _items(body) == sent
    # What ComplyCube holds of the try is kept for the resend, and so are our copies. The resend
    # is counted against the try.
    stored = await _stored_capture(sess, fx)
    assert stored[capture.RESENDS_KEY] == 1
    assert stored[capture.PROGRESS_KEY] == {
        "accepted": {key: uid for key, (uid, _s) in sent.items()},
        "document_id": failed["provider_document_id"],
        "document_type": "passport",
        "live_photo_id": failed["provider_live_photo_id"],
    }
    assert await _held_copies(sess, fx) == copies
    # Closed for the operators' queue, saying why, and no task is raised for it.
    closed = await _latest_check(sess, fx)
    assert closed["uid"] == failed["uid"]
    assert closed["decided_at"] is not None
    assert closed["decision_comment"] == routing.FRESH_TRY_COMMENT
    assert await _rows(sess, "SELECT id FROM tasks.tasks WHERE tenant_id = :t", t=ops) == []
    assert (await _step(sess, fx))["kyc"] == "pending"
    # The same send is queued again: the same try, document and slots.
    queued = await _queued(sess, fx)
    assert [row["status"] for row in queued] == ["delivered", "queued"]
    assert queued[1]["payload"] == queued[0]["payload"]

    # The worker starts a new check on the document and live photo ComplyCube already holds:
    # nothing is uploaded again.
    before = len(fake.calls)
    await _worker(sess, fx)
    assert f"GET /documents/{failed['provider_document_id']}" in fake.calls[before:]
    assert fake.posts(before) == ["POST /checks"]
    assert len(fake.documents) == 1 and len(fake.live_photos) == 1
    again = await _latest_check(sess, fx)
    assert again["uid"] != failed["uid"]
    assert again["status"] == "pending"
    assert again["attempt"] == 1
    assert again["provider_document_id"] == failed["provider_document_id"]
    assert again["provider_live_photo_id"] == failed["provider_live_photo_id"]
    assert await _checks(sess, fx) == 2
    assert (await _capture(sess, fx))["status"] == "submitted"

    # It fails again on the same try: an operator looks, rather than the check being paid for
    # again and again. Nothing is sent, the check stays in the queue, and the client still waits.
    assert await _result(sess, fx, "failed") == ["failure_task"]
    raised = await _rows(sess, "SELECT title FROM tasks.tasks WHERE tenant_id = :t", t=ops)
    assert [row["title"] for row in raised] == ["Identity check for Ivy Doyle could not run"]
    assert [row["status"] for row in await _queued(sess, fx)] == ["delivered", "delivered"]
    second = await _latest_check(sess, fx)
    assert second["status"] == "failed"
    assert second["decided_at"] is None
    stored = await _stored_capture(sess, fx)
    assert stored["status"] == capture.SUBMITTED
    assert stored[capture.RESENDS_KEY] == 1
    assert (await _capture(sess, fx))["status"] == "submitted"
    assert await _held_copies(sess, fx) == copies
    assert await _checks(sess, fx) == 2


@pytest.mark.asyncio
async def test_a_retake_after_a_name_change_checks_against_the_new_name(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The document is checked against the client ComplyCube holds, so a surname corrected between
    tries reaches ComplyCube before the new document does."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "passport")
    await _result(sess, fx, "rejected")
    await _ask(sess, fx, monkeypatch, ["document", "selfie"])
    [made] = fake.clients
    await sess.execute(
        text("UPDATE crm.contacts SET surname = 'Doyle-Hart' WHERE id = :c"), {"c": fx["contact"]}
    )

    before = len(fake.calls)
    await _try(sess, fx, fake, monkeypatch, "passport")

    update = f"POST /clients/{made}"
    assert [body for call, body in fake.bodies if call == update] == [
        {"personDetails": {"lastName": "Doyle-Hart"}}
    ]
    sent = fake.calls[before:]
    assert sent.index(update) < sent.index("POST /documents")


@pytest.mark.asyncio
@pytest.mark.parametrize("trouble", ["not_ours", "elsewhere", "refused"])
async def test_a_retake_whose_client_cannot_be_brought_up_to_date_goes_to_a_person(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    trouble: str,
) -> None:
    """A ComplyCube client filed under someone else or stamped by another deployment, or
    ComplyCube refusing the corrected details:
    new photos fix neither, so the client is not asked for them. Nothing is sent, and the submit
    fails for good, which reaches the error tracker."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "passport")
    await _result(sess, fx, "rejected")
    await _ask(sess, fx, monkeypatch, ["document", "selfie"])
    [made] = fake.clients
    await sess.execute(
        text("UPDATE crm.contacts SET surname = 'Doyle-Hart' WHERE id = :c"), {"c": fx["contact"]}
    )
    if trouble == "not_ours":
        fake.clients[made]["externalId"] = "someone-else"
    elif trouble == "elsewhere":
        fake.clients[made]["metadata"] = {"graphene_env": "another-deployment"}
    else:
        fake.refuse = 422
        fake.refuse_path = f"/clients/{made}"
        fake.refusal_message = "personDetails.lastName is not valid"

    before = len(fake.calls)
    with pytest.raises(PermanentDispatchError) as raised:
        await _try(sess, fx, fake, monkeypatch, "passport")

    if trouble == "refused":
        assert "personDetails.lastName is not valid" in str(raised.value)
    assert not any(
        call.startswith(("POST /documents", "POST /livePhotos")) for call in fake.calls[before:]
    )
    assert (await _capture(sess, fx))["status"] != "retake"


@pytest.mark.asyncio
async def test_a_document_complycube_refuses_is_asked_for_again_without_counting(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = 422
    fake.refusal_message = "  Image resolution is too low. " + "x" * 400
    with caplog.at_level(logging.WARNING, logger=identity.logger.name):
        body = await _try(sess, fx, fake, monkeypatch, "passport")
    assert body["status"] == "retake"
    assert body["attempt"] == 1
    assert "POST /checks" not in fake.calls
    assert await _checks(sess, fx) == 0
    # Not a failure to retry: the row is done, and the document is asked for again. The selfie is
    # not: we hold its copy.
    assert [row["status"] for row in await _queued(sess, fx)] == ["delivered"]
    assert {key for key, (_uid, status) in _items(body).items() if status == "open"} == {
        capture.FRONT,
        capture.BACK,
    }
    assert set(await _held_copies(sess, fx)) == {capture.SELFIE}

    # Kept on the capture for operations, trimmed, with when it happened.
    error = (await _stored_capture(sess, fx))[capture.PROVIDER_ERROR_KEY]
    assert error["message"] == fake.refusal_message.strip()[: identity.PROVIDER_ERROR_LIMIT]
    assert len(error["message"]) == identity.PROVIDER_ERROR_LIMIT
    assert error["status_code"] == 422
    assert error["at"]
    # The client's own read never carries it.
    assert capture.PROVIDER_ERROR_KEY not in body
    assert "resolution" not in json.dumps(body)
    # Logged at warning level with ComplyCube's message.
    refused = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Image resolution is too low" in r.getMessage() for r in refused)

    # A later try carries it until another refusal replaces it.
    accepted = FakeComplyCube(fx["nonce"] + "ok")
    accepted.clients = fake.clients  # the client the refused try made is still there
    await _try(sess, fx, accepted, monkeypatch, "passport")
    kept = await _stored_capture(sess, fx)
    assert kept["status"] == capture.SUBMITTED
    assert kept[capture.PROVIDER_ERROR_KEY] == error
    assert capture.PROVIDER_ERROR_KEY not in await _capture(sess, fx)
    assert bucket == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("copy_kept", [True, False])
async def test_a_refused_document_side_asks_for_both_sides_and_not_a_selfie_we_hold(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    copy_kept: bool,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = 422
    fake.refuse_path = f"/documents/doc_{fx['nonce']}"
    sent = _items(await _capture(sess, fx))
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence")
    # The front was refused: nothing after it was sent, so ComplyCube never got the selfie.
    assert "POST /livePhotos" not in fake.calls
    assert not any(call.endswith("/upload/back") for call in fake.calls)

    assert body["status"] == "retake"
    assert body["attempt"] == 1
    # The try keeps the selfie, and with it the document it named.
    assert body["document_type"] == "driving_licence"
    now = _items(body)
    # Both sides are asked for again. The selfie is not: we hold its copy, so it is listed as sent.
    for key in (capture.FRONT, capture.BACK):
        assert now[key][1] == "open"
        assert now[key][0] != sent[key][0]
    assert now[capture.SELFIE] == (sent[capture.SELFIE][0], "fulfilled")
    stored = await _stored_capture(sess, fx)
    # Nothing ComplyCube holds is kept. Of our copies, the refused sides' go and the selfie's stays.
    assert capture.PROGRESS_KEY not in stored
    assert await _held_copies(sess, fx) == {capture.SELFIE: sent[capture.SELFIE][0]}
    assert stored[capture.PROVIDER_ERROR_KEY]["status_code"] == 422
    assert stored[capture.PROVIDER_ERROR_KEY]["message"] == "unreadable"
    assert capture.PROVIDER_ERROR_KEY not in body and "unreadable" not in json.dumps(body)

    if not copy_kept:
        # Without our copy the selfie is asked for again, and the submit needs it.
        await sess.execute(
            text("DELETE FROM compliance.identity_photos WHERE slot_uid = :s"),
            {"s": sent[capture.SELFIE][0]},
        )
        listed = _items(await _capture(sess, fx))
        assert listed[capture.SELFIE] == (sent[capture.SELFIE][0], "open")
        with pytest.raises(HTTPException) as exc:
            await _submit(sess, fx, "driving_licence", capture.FRONT, capture.BACK)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Send every photo before you start the check"

    # The client sends the new sides alone while we hold the selfie, else all three.
    keys = (capture.FRONT, capture.BACK) if copy_kept else capture.KEYS
    fake.refuse = None
    before = len(fake.calls)
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=keys)
    assert body["status"] == "submitted"
    check = await _latest_check(sess, fx)
    new = str(check["provider_document_id"])
    # The new document's sides and the selfie ComplyCube never got.
    assert fake.posts(before) == [
        "POST /documents",
        f"POST /documents/{new}/upload/front",
        f"POST /documents/{new}/upload/back",
        "POST /livePhotos",
        "POST /checks",
    ]
    assert fake.live_photos == {str(check["provider_live_photo_id"]): SELFIE_JPEG}
    assert check["attempt"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_type", "refusal"), [("driving_licence", 422), ("passport", 400)]
)
async def test_a_refused_selfie_asks_for_the_selfie_alone_and_reuses_the_document(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    refusal: int,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = refusal
    fake.refuse_path = "/livePhotos"
    sent = _items(await _capture(sess, fx))
    sides = [key for key in capture.needed_keys(document_type) if key != capture.SELFIE]

    body = await _try(sess, fx, fake, monkeypatch, document_type)

    (document_id,) = fake.documents
    assert "POST /checks" not in fake.calls
    assert await _checks(sess, fx) == 0
    assert body["status"] == "retake"
    assert body["attempt"] == 1
    # The document's photos ComplyCube accepted stay, and fix the document; only the selfie is
    # asked for again.
    assert body["document_type"] == document_type
    now = _items(body)
    assert list(now) == [*sides, capture.SELFIE]
    for key in sides:
        assert now[key] == (sent[key][0], "fulfilled")
    assert now[capture.SELFIE][1] == "open"
    assert now[capture.SELFIE][0] != sent[capture.SELFIE][0]
    # Our copy of the refused selfie goes; the document's stay.
    assert await _held_copies(sess, fx) == {key: sent[key][0] for key in sides}
    # What ComplyCube holds, and why it refused, are operations': never in the client's read.
    for key in (capture.PROGRESS_KEY, capture.PROVIDER_ERROR_KEY, document_id):
        assert key not in json.dumps(body)
    stored = await _stored_capture(sess, fx)
    assert stored[capture.PROGRESS_KEY] == {
        "document_id": document_id,
        "document_type": document_type,
        "accepted": {key: sent[key][0] for key in sides},
    }
    assert stored[capture.PROVIDER_ERROR_KEY]["status_code"] == refusal

    # The kept photos fix the document: the retake cannot become another.
    other = "national_id" if document_type == "passport" else "passport"
    with pytest.raises(HTTPException) as exc:
        await _submit(sess, fx, other, capture.SELFIE)
    assert exc.value.status_code == 409
    assert exc.value.detail == api.SAME_DOCUMENT_NEEDED

    fake.refuse = None
    before = len(fake.calls)
    body = await _try(sess, fx, fake, monkeypatch, document_type, keys=(capture.SELFIE,))
    assert body["status"] == "submitted"
    resubmit = fake.calls[before:]
    # The same ComplyCube document, nothing uploaded to it again, and no orphan.
    assert f"GET /documents/{document_id}" in resubmit
    assert fake.posts(before) == ["POST /livePhotos", "POST /checks"]
    assert fake.calls.count("POST /documents") == 1
    assert list(fake.documents) == [document_id]
    check = await _latest_check(sess, fx)
    assert check["provider_document_id"] == document_id
    assert check["attempt"] == 1


@pytest.mark.asyncio
async def test_a_send_complycube_could_not_take_is_tried_again_and_reuses_what_it_holds(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComplyCube failing (not refusing) moves nothing on: the worker raises so the outbox sends
    the row again, the client keeps waiting, and what ComplyCube accepted before it failed is
    kept, so the next send makes no second document or live photo."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)
    sent = _items(await _capture(sess, fx))
    _answered(await _submit(sess, fx, "driving_licence"))

    # It fails after the document is made and both its sides are in.
    fake.refuse = 500
    fake.refuse_path = "/livePhotos"
    with pytest.raises(identity.ProviderUnavailable):
        await _worker(sess, fx)
    (document_id,) = fake.documents
    assert set(fake.documents[document_id]) == {"front", "back"}
    stored = await _stored_capture(sess, fx)
    assert stored["status"] == capture.SUBMITTED
    assert stored["attempt"] == 1
    assert stored[capture.PROGRESS_KEY] == {
        "accepted": {capture.FRONT: sent[capture.FRONT][0], capture.BACK: sent[capture.BACK][0]},
        "document_id": document_id,
        "document_type": "driving_licence",
    }
    # Not a refusal of the photos: nothing is recorded as one, nothing started, and the client is
    # asked for nothing. The row waits to be sent again, with every photo still held.
    assert capture.PROVIDER_ERROR_KEY not in stored
    assert await _checks(sess, fx) == 0
    assert (await _capture(sess, fx))["status"] == "submitted"
    assert [row["status"] for row in await _queued(sess, fx)] == ["queued"]
    assert await _held_copies(sess, fx) == {key: uid for key, (uid, _s) in sent.items()}

    # It fails again after the live photo is made: the held document is reused, not made again.
    fake.refuse_path = "/checks"
    before = len(fake.calls)
    with pytest.raises(identity.ProviderUnavailable):
        await _worker(sess, fx)
    assert f"GET /documents/{document_id}" in fake.calls[before:]
    assert fake.posts(before) == ["POST /livePhotos", "POST /checks"]
    (live_photo_id,) = fake.live_photos
    assert (await _stored_capture(sess, fx))[capture.PROGRESS_KEY] == {
        "accepted": {key: uid for key, (uid, _s) in sent.items()},
        "document_id": document_id,
        "document_type": "driving_licence",
        "live_photo_id": live_photo_id,
    }

    # ComplyCube is back: the same row starts the check with what it already holds.
    fake.refuse = None
    before = len(fake.calls)
    await _worker(sess, fx)
    assert fake.posts(before) == ["POST /checks"]
    assert list(fake.documents) == [document_id]
    assert list(fake.live_photos) == [live_photo_id]
    check = await _latest_check(sess, fx)
    assert check["provider_document_id"] == document_id
    assert check["provider_live_photo_id"] == live_photo_id
    assert check["attempt"] == 1
    assert [row["status"] for row in await _queued(sess, fx)] == ["delivered"]
    assert bucket == {}


@pytest.mark.asyncio
async def test_a_send_with_complycube_not_configured_is_tried_again(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    _answered(await _submit(sess, fx, "passport"))

    def not_configured() -> ScreeningClient:
        raise ComplyCubeNotConfigured()

    monkeypatch.setattr(identity, "require_client", not_configured)
    with pytest.raises(identity.ProviderUnavailable):
        await _worker(sess, fx)
    stored = await _stored_capture(sess, fx)
    assert stored["status"] == capture.SUBMITTED
    assert capture.PROGRESS_KEY not in stored
    assert set(await _held_copies(sess, fx)) == {capture.FRONT, capture.SELFIE}
    assert await _checks(sess, fx) == 0

    # Configured again: the row the outbox kept starts the check.
    await _run_submit(sess, fx, FakeComplyCube(fx["nonce"]), monkeypatch)
    assert await _checks(sess, fx) == 1
    assert [row["status"] for row in await _queued(sess, fx)] == ["delivered"]


@pytest.mark.asyncio
async def test_a_document_complycube_lost_is_made_again_from_our_copy(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selfie is sent again for a document ComplyCube has since lost: the document is made
    again from the sides we hold, and the client is asked for nothing."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = 422
    fake.refuse_path = "/livePhotos"
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    (old,) = fake.documents

    fake.refuse = None
    fake.gone.add(old)
    before = len(fake.calls)
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))

    assert body["status"] == "submitted"
    assert body["attempt"] == 1
    check = await _latest_check(sess, fx)
    new = str(check["provider_document_id"])
    assert new != old
    assert f"GET /documents/{old}" in fake.calls[before:]
    assert fake.posts(before) == [
        "POST /documents",
        f"POST /documents/{new}/upload/front",
        f"POST /documents/{new}/upload/back",
        "POST /livePhotos",
        "POST /checks",
    ]
    assert fake.documents[new] == {
        "front": ("front.jpg", FRONT_JPEG),
        "back": ("back.jpg", BACK_JPEG),
    }
    stored = await _stored_capture(sess, fx)
    assert stored[capture.PROGRESS_KEY]["document_id"] == new


@pytest.mark.asyncio
async def test_a_document_complycube_lost_is_asked_for_again_when_we_hold_no_copy(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to make it again from: both sides of the document are asked for again at the same
    attempt, as for a refusal. The selfie just sent is held, so it is not."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = 422
    fake.refuse_path = "/livePhotos"
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    (old,) = fake.documents
    retake = _items(await _capture(sess, fx))
    await sess.execute(
        text("DELETE FROM compliance.identity_photos WHERE case_id = :c"), {"c": fx["case"]}
    )

    fake.refuse = None
    fake.gone.add(old)
    before = len(fake.calls)
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))
    (client,) = fake.clients
    # The bound client, checked before it is used, then the document; the 404 is the document's,
    # and ComplyCube still holds the client, so it is not bound again.
    assert fake.calls[before:] == [
        f"GET /clients/{client}",
        f"GET /documents/{old}",
        f"GET /clients/{client}",
    ]
    assert body["status"] == "retake"
    assert body["attempt"] == 1
    now = _items(body)
    for key in (capture.FRONT, capture.BACK):
        assert now[key][1] == "open"
        assert now[key][0] != retake[key][0]
    assert now[capture.SELFIE] == (retake[capture.SELFIE][0], "fulfilled")
    stored = await _stored_capture(sess, fx)
    assert capture.PROGRESS_KEY not in stored
    assert stored[capture.PROVIDER_ERROR_KEY]["status_code"] == 404
    assert await _held_copies(sess, fx) == {capture.SELFIE: retake[capture.SELFIE][0]}
    assert await _checks(sess, fx) == 0

    # The next send makes the document again from the client's new sides, with the held selfie.
    before = len(fake.calls)
    await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.FRONT, capture.BACK))
    check = await _latest_check(sess, fx)
    new = str(check["provider_document_id"])
    assert new != old
    assert fake.posts(before) == [
        "POST /documents",
        f"POST /documents/{new}/upload/front",
        f"POST /documents/{new}/upload/back",
        "POST /livePhotos",
        "POST /checks",
    ]
    assert check["attempt"] == 1


@pytest.mark.asyncio
async def test_a_photo_we_no_longer_hold_is_asked_for_again_at_the_same_try(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)
    sent = _items(await _capture(sess, fx))
    _answered(await _submit(sess, fx, "driving_licence"))
    await sess.execute(
        text("DELETE FROM compliance.identity_photos WHERE slot_uid = :s"),
        {"s": sent[capture.SELFIE][0]},
    )

    await _worker(sess, fx)

    # Nothing goes to ComplyCube: the client is asked for the selfie again, at the same try.
    assert fake.calls == []
    assert await _checks(sess, fx) == 0
    body = await _capture(sess, fx)
    assert body["status"] == "retake"
    assert body["attempt"] == 1
    now = _items(body)
    assert now[capture.SELFIE][1] == "open"
    assert now[capture.SELFIE][0] != sent[capture.SELFIE][0]
    for key in (capture.FRONT, capture.BACK):
        assert now[key][0] == sent[key][0]
    assert await _held_copies(sess, fx) == {
        capture.FRONT: sent[capture.FRONT][0],
        capture.BACK: sent[capture.BACK][0],
    }

    # The client sends what is open, and the check starts.
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    assert await _checks(sess, fx) == 1
    assert len(fake.documents) == 1


@pytest.mark.asyncio
async def test_a_send_for_a_try_the_step_moved_past_is_dropped(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, fake, monkeypatch, "passport")
    stale = (await _queued(sess, fx))[-1]["payload"]
    await _result(sess, fx, "rejected")
    calls = len(fake.calls)
    await identity.submit(sess, fx["tenant"], stale)
    assert len(fake.calls) == calls
    assert await _checks(sess, fx) == 1
    # A row that does not say what to send is never tried again.
    with pytest.raises(PermanentDispatchError):
        await identity.submit(sess, fx["tenant"], {"contact_id": fx["contact"]})


@pytest.mark.asyncio
async def test_a_send_the_outbox_gives_up_on_asks_the_client_for_the_photos_again(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComplyCube down past every retry: the row dead-letters, and the client is asked to send the
    photos again at the same attempt, rather than waiting for ever with their copies held for a
    send that will never happen. Sent again once ComplyCube is back, the check starts."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    fake = FakeComplyCube(fx["nonce"])
    _use(fake, monkeypatch)
    sent = _items(await _capture(sess, fx))
    _answered(await _submit(sess, fx, "driving_licence"))

    # The row's last try, and the only row due, so the dispatcher's turn is this one.
    fake.refuse = 500
    await sess.execute(
        text(
            "UPDATE flows.outbox SET next_retry = 'infinity' "
            "WHERE status = 'queued' AND NOT (tenant_id = :t AND event_type = :e)"
        ),
        {"t": fx["tenant"], "e": identity.EVENT},
    )
    await sess.execute(
        text(
            "UPDATE flows.outbox SET attempts = max_attempts - 1, next_retry = '-infinity' "
            "WHERE tenant_id = :t AND event_type = :e"
        ),
        {"t": fx["tenant"], "e": identity.EVENT},
    )
    await dispatcher.dispatch_due(sess, now=datetime.now(UTC), limit=1)

    assert [row["status"] for row in await _queued(sess, fx)] == ["dead_letter"]
    body = await _capture(sess, fx)
    assert body["status"] == capture.RETAKE
    assert body["attempt"] == 1
    assert body["last_reason"] == identity.SEND_FAILED_REASON
    # ComplyCube holds none of them, so every photo is asked for again, in a new slot, and no copy
    # of the old ones stays.
    reopened = _items(body)
    assert all(reopened[key][1] == "open" for key in capture.KEYS)
    assert all(reopened[key][0] != sent[key][0] for key in capture.KEYS)
    assert await _held_copies(sess, fx) == {}
    assert await _checks(sess, fx) == 0

    fake.refuse = None
    body = await _try(sess, fx, fake, monkeypatch)
    assert body["status"] == "submitted"
    assert await _checks(sess, fx) == 1


@pytest.mark.asyncio
async def test_the_worker_drops_the_photos_of_a_step_that_finished(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop sweep: a step cancelled with its journey loses its photos; a step still taking
    them keeps them."""
    sess = service_session
    cancelled = await _seed(sess)
    await _instantiate(sess, cancelled, "complycube")
    _answered(await _submit(sess, cancelled, "passport"))
    open_step = await _seed(sess)
    await _instantiate(sess, open_step, "complycube")
    _answered(await _submit(sess, open_step, "passport"))
    await sess.execute(
        text("UPDATE flows.case_steps SET status = 'cancelled' WHERE case_id = :c"),
        {"c": cancelled["case"]},
    )

    assert await idv_held.release_finished(sess) >= 2

    assert await _held_copies(sess, cancelled) == {}
    assert set(await _held_copies(sess, open_step)) == {capture.FRONT, capture.SELFIE}


def _staff(*permissions: str) -> Principal:
    return Principal(
        user_id="wos_staff", organisation_id="org_1", role="adviser", permissions=list(permissions)
    )


async def _journey_step(
    sess: AsyncSession, fx: dict[str, Any], principal: Principal
) -> dict[str, Any]:
    """The identity step as ``GET /applications/{uid}`` gives it to ``principal``."""
    exec_uid = await _scalar(
        sess, "SELECT uid FROM flows.executions WHERE id = :e", e=fx["execution"]
    )
    # The fixture journey is this firm's. ``contacts:read_all`` is the book gate's whole-firm
    # pass; the step payload still follows the permissions the test actually named.
    widened = Principal(
        user_id=principal.user_id,
        organisation_id=principal.organisation_id,
        role=principal.role,
        permissions=[*principal.permissions, CONTACTS_READ_ALL.slug],
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=widened)
    out = await api.get_application(exec_uid, widened, ctx)
    steps = [s for c in cast(list[Any], out["cases"]) for s in c["steps"]]
    return next(s for s in steps if s["step_key"] == "idv")


async def _open_client_steps(sess: AsyncSession, fx: dict[str, Any]) -> int:
    exec_uid = await _scalar(
        sess, "SELECT uid FROM flows.executions WHERE id = :e", e=fx["execution"]
    )
    firm = _staff(CASES_READ.slug, CONTACTS_READ_ALL.slug)
    rows = await api.list_applications(
        firm, FirmContext(session=sess, tenant_id=fx["tenant"], principal=firm)
    )
    return int(next(r for r in rows if r["uid"] == exec_uid)["open_client_steps"])


@pytest.mark.asyncio
async def test_the_firm_sees_a_review_with_graphene_and_only_operations_see_a_refusal(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    firm = _staff(CASES_READ.slug)
    operator = _staff(CASES_READ.slug, TENANT_PORTAL_READ.slug)
    taking = await _journey_step(sess, fx, firm)
    assert taking["identity_review"] is False
    assert await _open_client_steps(sess, fx) == 1

    fake = FakeComplyCube(fx["nonce"])
    fake.refuse = 422
    fake.refusal_message = "Image resolution is too low."
    await _try(sess, fx, fake, monkeypatch, "passport")
    step = await capture.step_for_check(sess, fx["kyc"])
    assert step is not None
    await capture.set_status(sess, step, capture.REVIEW)

    reviewing = await _journey_step(sess, fx, firm)
    assert reviewing["identity_review"] is True
    assert reviewing["assignee"] == "client"
    # Status only for the firm: never ComplyCube's words.
    assert "identity_provider_error" not in reviewing
    assert "resolution" not in json.dumps(reviewing, default=str)
    # Nothing on it is the client's to do while Graphene reviews it.
    assert await _open_client_steps(sess, fx) == 0

    seen = await _journey_step(sess, fx, operator)
    assert seen["identity_provider_error"]["message"] == "Image resolution is too low."
    assert seen["identity_provider_error"]["status_code"] == 422
    assert seen["identity_provider_error"]["at"]

    # A new try hands the step back to the client.
    held = await capture.step_for_check(sess, fx["kyc"])
    assert held is not None
    await capture.new_try(sess, held, attempt=2, last_reason=None)
    assert (await _journey_step(sess, fx, firm))["identity_review"] is False
    assert await _open_client_steps(sess, fx) == 1


@pytest.mark.asyncio
async def test_a_fraud_signal_never_reaches_the_client(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    first = FakeComplyCube(fx["nonce"])
    await _try(sess, fx, first, monkeypatch, "passport")
    fraud = {
        "outcome": "rejected",
        "breakdown": {
            "imageQuality": {"glare": "clear"},
            "documentAuthenticity": {"outcome": "rejected", "digitalTampering": "rejected"},
            "faceAnalysis": {"faceComparison": "rejected"},
        },
    }
    await _result(sess, fx, "rejected", result=fraud)
    body = await _capture(sess, fx)
    assert body["status"] == "review"
    assert body["last_reason"] is None
    for word in ("Tampering", "faceComparison", "documentAuthenticity", "rejected"):
        assert word not in json.dumps(body)

    # The operator asks for new photos: the client reads only the reason chosen for them.
    await _ask(sess, fx, monkeypatch, ["document", "selfie"], "glare", comment="Looks tampered")
    body = await _capture(sess, fx)
    assert body["last_reason"] == identity.PHOTO_REQUEST_REASONS["glare"]
    for word in ("Tampering", "tampered", "faceComparison", "documentAuthenticity", "rejected"):
        assert word not in json.dumps(body)

    # Nor does a later submit's answer carry any of it.
    second = FakeComplyCube(fx["nonce"] + "b")
    second.clients = first.clients  # ComplyCube still holds the client the first try made
    body = await _try(sess, fx, second, monkeypatch, "passport")
    for word in ("Tampering", "tampered", "faceComparison", "documentAuthenticity", "rejected"):
        assert word not in json.dumps(body)


@pytest.mark.asyncio
async def test_choosing_a_person_takes_the_capture_away_and_drops_the_photos(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    before = await _stored_capture(sess, fx)
    # The client sent their photos; ComplyCube could not take them yet.
    _answered(await _submit(sess, fx, "passport"))
    assert set(await _held_copies(sess, fx)) == {capture.FRONT, capture.SELFIE}

    ctx = await _client(sess, fx)
    await api.my_identity_manual(
        body=api.IdentityChoice(step_key="idv", case_uid=fx["case_uid"]),
        principal=_principal(fx),
        ctx=ctx,
    )
    with pytest.raises(HTTPException) as exc:
        await _capture(sess, fx)
    assert exc.value.status_code == 404
    # A person checks the documents now: the photos held for the digital check go.
    assert await _held_copies(sess, fx) == {}
    # The queued send finds the step handed to a person, and sends nothing.
    fake = FakeComplyCube(fx["nonce"])
    await _run_submit(sess, fx, fake, monkeypatch)
    assert fake.calls == []
    # The photo slots are simply left unused: they were never on the client's checklist.
    assert capture.item_uids(await _stored_capture(sess, fx)) == capture.item_uids(before)
    # The checklist holds the two documents a person checks, on one request, still open.
    requests = await _rows(
        sess,
        "SELECT r.id, r.status, count(*) AS items FROM documents.request_items ri "
        "JOIN documents.requests r ON r.id = ri.request_id WHERE ri.tenant_id = :t "
        "GROUP BY r.id, r.status",
        t=fx["tenant"],
    )
    assert len(requests) == 1
    assert requests[0]["status"] == "pending"
    assert requests[0]["items"] == 2


@pytest.mark.asyncio
async def test_a_retired_creditsafe_step_queues_its_screen_on_complycube(
    service_session: AsyncSession,
) -> None:
    """A published version that still says `creditsafe` runs on ComplyCube: the step opens its
    pending check and queues the screen to run by itself, like any ComplyCube AML step."""
    sess = service_session
    fx = await _seed(sess, step_type="aml_check")
    event = await _instantiate(sess, fx, "creditsafe", step_type="aml_check")
    assert event.signal == StepSignal.WAITING
    kyc = (
        await _rows(sess, "SELECT status, provider FROM crm.kyc_checks WHERE id = :k", k=fx["kyc"])
    )[0]
    assert kyc == {"status": "pending", "provider": "complycube"}
    queued = await _rows(
        sess,
        "SELECT event_type, payload FROM flows.outbox WHERE tenant_id = :t AND event_type = :e",
        t=fx["tenant"],
        e="aml.screen.run",
    )
    assert [row["payload"] for row in queued] == [
        {"contact_id": fx["contact"], "kyc_check_id": fx["kyc"]}
    ]


# ── A journey's AML step held on a confirmed match ────────────────────────────────────────────


async def _aml_step(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, Any]:
    rows = await _rows(
        sess,
        "SELECT cs.status, cs.data ->> 'last_event' AS last_event, k.status AS kyc "
        "FROM flows.case_steps cs "
        "JOIN crm.kyc_checks k ON k.id = CAST(cs.data ->> 'kyc_check_id' AS bigint) "
        "WHERE cs.case_id = :c AND cs.step_key = 'aml'",
        c=fx["case"],
    )
    return rows[0]


async def _screened(sess: AsyncSession, fx: dict[str, Any], matches: int = 2) -> str:
    """The AML screen the step queued, run and referred by ComplyCube with ``matches`` matches
    nobody has decided yet. Returns the check's uid."""
    nonce = fx["nonce"]
    client = int(
        await _scalar(
            sess,
            "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id) "
            "VALUES (:t, :c, :p) RETURNING id",
            t=fx["tenant"],
            c=fx["contact"],
            p=f"cli_aml_{nonce}",
        )
    )
    check = (
        await sess.execute(
            text(
                "INSERT INTO compliance.checks (tenant_id, client_id, kind, status, "
                "  provider_check_id, origin, kyc_check_id) "
                "VALUES (:t, :c, 'aml', 'refer', :p, 'portal', :k) RETURNING id, uid"
            ),
            {"t": fx["tenant"], "c": client, "p": f"chk_aml_{nonce}", "k": fx["kyc"]},
        )
    ).one()
    for i in range(matches):
        await sess.execute(
            text(
                "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, name, "
                "  match_types, score) VALUES (:t, :c, :p, :n, ARRAY['pep'], :s)"
            ),
            {"t": fx["tenant"], "c": int(check.id), "p": f"m{i}", "n": f"Match {i}", "s": 90 - i},
        )
    return str(check.uid)


async def _decide_match(
    sess: AsyncSession,
    fx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    uid: str,
    match: int,
    decision: str,
) -> dict[str, Any]:
    """An operator confirms or dismisses one match (``m<match>``) in the console."""
    monkeypatch.setattr(aml_router, "require_client", FakeComplyCube(fx["nonce"]).client)
    ctx = await _console(sess, fx, monkeypatch)
    match_uid = await _scalar(
        sess,
        "SELECT m.uid FROM compliance.matches m JOIN compliance.checks c ON c.id = m.check_id "
        "WHERE c.uid = :u AND m.provider_match_id = :p",
        u=uid,
        p=f"m{match}",
    )
    return await aml_router.decide_match(
        uid,
        str(match_uid),
        aml_router.MatchDecisionRequest(decision=cast(Any, decision), comment="Checked"),
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_a_confirmed_match_holds_the_aml_step_until_an_operator_allows_it(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed match no longer fails the journey: the step keeps waiting and the kyc row stays
    pending while Graphene reviews. Allowing the journey to continue passes both."""
    sess = service_session
    fx = await _seed(sess, step_type="aml_check")
    await _instantiate(sess, fx, "complycube", step_type="aml_check")
    waiting = await _aml_step(sess, fx)
    assert waiting["kyc"] == "pending"
    assert waiting["status"] in ("pending", "waiting")
    uid = await _screened(sess, fx)

    await _decide_match(sess, fx, monkeypatch, uid, 0, "confirmed")
    held = await _decide_match(sess, fx, monkeypatch, uid, 1, "dismissed")
    assert held["check"]["status"] == "refer"
    assert held["check"]["decided_at"] is None
    # The step is untouched: still waiting, never failed as `aml_referred`.
    assert await _aml_step(sess, fx) == waiting
    row = await _console_row(sess, fx, monkeypatch)
    assert row["aml"]["uid"] == uid
    assert row["needs_attention"] is True

    ctx = await _console(sess, fx, monkeypatch)
    allowed = await aml_router.allow_journey(
        uid, aml_router.AllowJourneyRequest(comment="Known PEP, risk accepted"), ctx=ctx
    )
    assert allowed["check"]["status"] == "refer"
    assert allowed["check"]["decided_at"] is not None
    assert allowed["check"]["decision_comment"] == (
        "1 of 2 matches confirmed. Allowed to continue: Known PEP, risk accepted"
    )
    step = await _aml_step(sess, fx)
    assert step["kyc"] == "passed"
    assert step["status"] == "fulfilled"
    assert step["last_event"] == "screened_clear"
    assert (await _console_row(sess, fx, monkeypatch))["needs_attention"] is False


@pytest.mark.asyncio
async def test_a_held_aml_step_passes_when_its_confirmed_match_is_dismissed_after_all(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess, step_type="aml_check")
    await _instantiate(sess, fx, "complycube", step_type="aml_check")
    waiting = await _aml_step(sess, fx)
    uid = await _screened(sess, fx)
    await _decide_match(sess, fx, monkeypatch, uid, 0, "confirmed")
    await _decide_match(sess, fx, monkeypatch, uid, 1, "dismissed")
    assert await _aml_step(sess, fx) == waiting

    cleared = await _decide_match(sess, fx, monkeypatch, uid, 0, "dismissed")
    assert cleared["check"]["status"] == "clear"
    step = await _aml_step(sess, fx)
    assert step["kyc"] == "passed"
    assert step["status"] == "fulfilled"
    assert step["last_event"] == "screened_clear"


async def _offered(sess: AsyncSession, fx: dict[str, Any]) -> bool:
    """Whether the client's journey (``GET /applications/me``) offers the identity step now."""
    out = await api.my_onboarding(principal=_principal(fx), ctx=await _client(sess, fx))
    step = next(s for s in out["steps"] if s["step_key"] == "idv")
    assert "capture_status" not in step
    return bool(step["available"])


@pytest.mark.asyncio
async def test_the_journey_offers_the_photos_only_while_they_are_the_clients_to_take(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sent photos are with the check: the step stays waiting for the verdict, but it is not
    offered to the client again until an operator asks for new photos."""
    sess = service_session
    fx = await _seed(sess)
    await _instantiate(sess, fx, "complycube")
    assert await _offered(sess, fx) is True

    _use(FakeComplyCube(fx["nonce"]), monkeypatch)
    _answered(await _submit(sess, fx, "passport"))
    assert (await _step(sess, fx))["status"] in ("pending", "waiting")
    assert await _offered(sess, fx) is False

    await _worker(sess, fx)
    await _result(sess, fx, "rejected")
    assert (await _capture(sess, fx))["status"] == "review"
    assert await _offered(sess, fx) is False

    await _ask(sess, fx, monkeypatch, ["selfie"])
    assert (await _capture(sess, fx))["status"] == "retake"
    assert await _offered(sess, fx) is True
