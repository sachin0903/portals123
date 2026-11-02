"""Identity photos an operator uploads for a client (``POST /aml/clients/{key}/identity-photos``).

Against real Postgres with a fake ComplyCube, on the identity capture tests' own journey:

* an upload answers the try a journey step waits on: a try held for review is closed under the
  operator and the next try is the upload's, already sent, so the client is asked for nothing;
* a photo left out is the one ComplyCube holds: the document, or the live photo;
* with no journey waiting, the check opens a new identity kyc row, which a clear passes;
* nothing is uploaded while a check runs, nothing held can be kept without being held, and a photo
  ComplyCube refuses is the operator's to fix, with nothing recorded;
* the act is an ``aml.`` event that names the photos only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from io import BytesIO
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import uploads
from app.config import settings
from app.idv import capture
from app.routers import aml as aml_router
from tests.test_identity_capture import (
    GIF,
    PDF,
    PHOTOS,
    FakeComplyCube,
    _capture,
    _console,
    _held_copies,
    _instantiate,
    _latest_check,
    _result,
    _rows,
    _scalar,
    _seed,
    _step,
    _stored_capture,
    _submit,
    _try,
    _use,
    _worker,
)

NOTE = "The client emailed a new passport on 8 October."


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> None:
    """ComplyCube configured, and a master key to seal the client's own photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


async def _upload(
    sess: AsyncSession,
    fx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeComplyCube,
    *keys: str,
    document_type: str | None = "passport",
    comment: str = NOTE,
    files: dict[str, tuple[bytes, str]] | None = None,
    permissions: list[str] | None = None,
) -> dict[str, object]:
    """The console's upload of ``keys`` for the client, as the admin portal sends it."""
    _use(fake, monkeypatch)
    monkeypatch.setattr(aml_router, "require_client", fake.client)
    ctx = await _console(sess, fx, monkeypatch)
    if permissions is not None:
        ctx = replace(ctx, principal=ctx.principal.model_copy(update={"permissions": permissions}))
    chosen = {key: (files or {}).get(key, PHOTOS[key]) for key in keys}
    sent = {
        key: UploadFile(filename=name, file=BytesIO(data)) for key, (data, name) in chosen.items()
    }
    return await aml_router.upload_identity_photos(
        fx["contact_uid"],
        comment=comment,
        document_type=cast(Any, document_type),
        issuing_country=None,
        idv_document_front=sent.get(capture.FRONT),
        idv_document_back=sent.get(capture.BACK),
        idv_selfie=sent.get(capture.SELFIE),
        ctx=ctx,
    )


async def _check_row(sess: AsyncSession, uid: str) -> dict[str, Any]:
    (row,) = await _rows(
        sess,
        "SELECT status, attempt, kyc_check_id, decided_at, decided_by, decision_comment, "
        "  uploaded_by, upload_comment, provider_document_id, provider_live_photo_id "
        "FROM compliance.checks WHERE uid = :u",
        u=uid,
    )
    return row


async def _held_try(
    sess: AsyncSession, fx: dict[str, Any], fake: FakeComplyCube, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The client's licence try, which ComplyCube did not clear: held for an operator."""
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer")
    return await _latest_check(sess, fx)


@pytest.mark.asyncio
async def test_an_upload_answers_a_try_held_for_review(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    page = await aml_router.get_client(fx["contact_uid"], ctx=await _console(sess, fx, monkeypatch))
    assert page["identity_upload"] == {
        "allowed": True,
        "blocked": None,
        "document": {"document_type": "driving_licence"},
        "selfie": True,
        "issuing_country": "GB",
        "journey": True,
    }
    since = len(fake.calls)

    answer = await _upload(sess, fx, monkeypatch, fake, capture.FRONT, capture.SELFIE)

    assert answer["photos"] == ["document", "selfie"]
    assert answer["document_type"] == "passport"
    assert fake.posts(since) == [
        "POST /documents",
        f"POST /documents/doc_{fx['nonce']}_{fake.counter - 2}/upload/front",
        "POST /livePhotos",
        "POST /checks",
    ]
    made = await _check_row(sess, str(answer["check_uid"]))
    operator = f"ops_{fx['nonce']}@t.test"
    assert made["status"] == "pending"
    assert made["attempt"] == 2
    assert made["kyc_check_id"] == fx["kyc"]
    assert (made["uploaded_by"], made["upload_comment"]) == (operator, NOTE)
    # The held try is closed under the operator, its status standing, as asking for photos does.
    closed = await _check_row(sess, str(held["uid"]))
    assert closed["status"] == "refer"
    assert closed["decided_at"] is not None
    assert (closed["decided_by"], closed["decision_comment"]) == (operator, NOTE)
    # The step's next try is the upload's, already sent: the client is asked for nothing.
    body = await _capture(sess, fx)
    assert body["status"] == "submitted"
    assert body["attempt"] == 2
    assert body["last_reason"] is None
    assert body["document_type"] == "passport"
    assert NOTE not in json.dumps(body)
    stored = await _stored_capture(sess, fx)
    slots = capture.item_uids(stored)
    assert stored[capture.PROGRESS_KEY] == {
        "accepted": {key: slots[key] for key in (capture.FRONT, capture.SELFIE)},
        "document_id": made["provider_document_id"],
        "document_type": "passport",
        "live_photo_id": made["provider_live_photo_id"],
    }
    # Our copy of the uploaded photos is kept, under the new try, until the check is decided.
    assert await _held_copies(sess, fx) == {
        key: slots[key] for key in (capture.FRONT, capture.SELFIE)
    }

    await _result(sess, fx, "clear")
    step = await _step(sess, fx)
    assert step["kyc"] == "passed"
    assert await _held_copies(sess, fx) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("left_out", ["document", "selfie"])
async def test_a_photo_left_out_is_the_one_complycube_holds(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
    left_out: str,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    since = len(fake.calls)

    if left_out == "document":
        answer = await _upload(sess, fx, monkeypatch, fake, capture.SELFIE, document_type=None)
    else:
        answer = await _upload(
            sess, fx, monkeypatch, fake, capture.FRONT, capture.BACK, document_type="national_id"
        )

    made = await _check_row(sess, str(answer["check_uid"]))
    if left_out == "document":
        assert answer["document_type"] == "driving_licence"
        assert made["provider_document_id"] == held["provider_document_id"]
        assert made["provider_live_photo_id"] != held["provider_live_photo_id"]
        assert "POST /documents" not in fake.posts(since)
    else:
        assert answer["document_type"] == "national_id"
        assert made["provider_document_id"] != held["provider_document_id"]
        assert made["provider_live_photo_id"] == held["provider_live_photo_id"]
        assert "POST /livePhotos" not in fake.posts(since)
    assert (await _capture(sess, fx))["status"] == "submitted"


@pytest.mark.asyncio
async def test_an_upload_while_the_client_retakes_is_that_try(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After "Ask for new photos" the client is on try 2; the photos they emailed instead are it."""
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    await aml_router.request_photos(
        str(held["uid"]),
        aml_router.PhotoRequest(photos=["selfie"], reason="face", comment="Selfie is dark"),
        ctx=await _console(sess, fx, monkeypatch),
    )
    assert (await _capture(sess, fx))["status"] == "retake"

    answer = await _upload(sess, fx, monkeypatch, fake, capture.SELFIE, document_type=None)

    made = await _check_row(sess, str(answer["check_uid"]))
    assert made["attempt"] == 2
    # The held try was decided by the request already: the upload does not decide it again.
    assert (await _check_row(sess, str(held["uid"])))["decision_comment"] == "Selfie is dark"
    body = await _capture(sess, fx)
    assert (body["status"], body["attempt"]) == ("submitted", 2)


@pytest.mark.asyncio
async def test_with_no_journey_waiting_a_clear_verifies_the_client_again(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client passed; later their passport changed. The upload opens a new identity kyc row,
    and a clear passes it, with no horizon."""
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "clear")
    first_kyc = fx["kyc"]
    page = await aml_router.get_client(fx["contact_uid"], ctx=await _console(sess, fx, monkeypatch))
    assert page["identity_upload"]["journey"] is False

    answer = await _upload(sess, fx, monkeypatch, fake, capture.FRONT, document_type="passport")

    made = await _check_row(sess, str(answer["check_uid"]))
    assert made["attempt"] == 1
    assert made["kyc_check_id"] != first_kyc
    fx["kyc"] = made["kyc_check_id"]
    assert (
        await _scalar(sess, "SELECT status FROM crm.kyc_checks WHERE id = :k", k=fx["kyc"])
        == "pending"
    )
    await _result(sess, fx, "clear")
    (kyc,) = await _rows(
        sess, "SELECT status, valid_until FROM crm.kyc_checks WHERE id = :k", k=fx["kyc"]
    )
    assert kyc["status"] == "passed"
    assert kyc["valid_until"] is None


@pytest.mark.asyncio
async def test_nothing_is_uploaded_while_the_clients_photos_are_with_complycube(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    _use(fake, monkeypatch)
    await _submit(sess, fx, "passport")
    with pytest.raises(HTTPException) as raised:
        await _upload(sess, fx, monkeypatch, fake, capture.FRONT, capture.SELFIE)
    assert (raised.value.status_code, raised.value.detail) == (409, uploads.PHOTOS_SENDING)

    await _worker(sess, fx)
    with pytest.raises(HTTPException) as raised:
        await _upload(sess, fx, monkeypatch, fake, capture.FRONT, capture.SELFIE)
    assert (raised.value.status_code, raised.value.detail) == (409, uploads.CHECK_RUNNING)
    assert (
        len(
            await _rows(
                sess, "SELECT id FROM compliance.checks WHERE kyc_check_id = :k", k=fx["kyc"]
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_a_refused_upload_records_nothing(
    service_session: AsyncSession,
    complycube: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComplyCube refusing the photo, a missing side, nothing held to keep, a bad file, no note,
    and no ``aml:adjudicate``: each is answered before a check exists."""
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")

    async def refused(*keys: str, **kw: Any) -> tuple[int, str]:
        with pytest.raises(HTTPException) as raised:
            await _upload(sess, fx, monkeypatch, fake, *keys, **kw)
        return raised.value.status_code, str(raised.value.detail)

    # Nothing is held for this client yet, so nothing can be kept.
    assert await refused(capture.SELFIE, document_type=None) == (
        409,
        "ComplyCube holds no document for this client. Upload the document too.",
    )
    assert await refused(capture.FRONT) == (
        409,
        "ComplyCube holds no selfie for this client. Upload a selfie too.",
    )
    assert await refused(capture.FRONT, capture.SELFIE, document_type="driving_licence") == (
        422,
        "The document's back is missing.",
    )
    assert await refused(capture.FRONT, capture.BACK, capture.SELFIE) == (
        422,
        "A passport has no back.",
    )
    assert await refused(
        capture.FRONT, capture.SELFIE, files={capture.SELFIE: (PDF, "selfie.pdf")}
    ) == (422, "The selfie must be a JPG or PNG.")
    assert await refused(capture.FRONT, capture.SELFIE, files={capture.FRONT: (GIF, "f.gif")}) == (
        422,
        "The document's front must be a JPG, PNG or PDF.",
    )
    assert await refused(capture.FRONT, capture.SELFIE, comment="  ") == (
        422,
        "A comment is required.",
    )
    assert (await refused(capture.FRONT, capture.SELFIE, permissions=["aml:read", "aml:write"]))[
        0
    ] == 403
    fake.refuse = 422
    fake.refusal_message = "Image is too blurry"
    assert await refused(capture.FRONT, capture.SELFIE) == (
        422,
        "ComplyCube refused the document's front: Image is too blurry",
    )
    assert (
        await _rows(sess, "SELECT id FROM compliance.checks WHERE kyc_check_id = :k", k=fx["kyc"])
        == []
    )
    assert (await _capture(sess, fx))["status"] == "capturing"


def test_an_upload_is_an_aml_event_that_names_the_photos_only() -> None:
    from app.audit_actions import event_type_for, extract_response_subject
    from app.main import app
    from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES
    from tests.conftest import api_routes

    route = next(r for r in api_routes(app) if r.path == "/aml/clients/{key}/identity-photos")
    event_type = event_type_for(route)
    assert event_type == "aml.identity_photos_uploaded"
    assert event_type.startswith(FIRM_EXCLUDED_EVENT_PREFIXES)
    body = json.dumps(
        {"check_uid": "chk1", "photos": ["document", "selfie"], "document_type": "passport"}
    ).encode()
    assert extract_response_subject(event_type, body) == {"photos": ["document", "selfie"]}
