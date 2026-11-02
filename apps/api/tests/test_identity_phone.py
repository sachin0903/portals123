"""A one-time link so the identity photos can be taken on a phone.

The desktop asks for the link. Opening it spends the code and mints a session that can send
this step's photos and nothing else. The code and the token are never stored, and neither
they nor their hashes reach the activity trail.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.config import settings
from app.idv import capture as idv_capture
from app.idv import phone
from app.main import app
from app.routers import applications as api
from app.routers import identity_phone
from app.routers.applications import IdentityChoice
from tests.test_identity_capture import (
    FRONT_JPEG,
    SELFIE_JPEG,
    _capture,
    _client,
    _held_copies,
    _instantiate,
    _seed,
)


def test_a_loopback_portal_is_rewritten_to_the_lan_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phone, "lan_address", lambda: "192.168.1.20")
    assert phone.reachable_base("http://localhost:5173") == "http://192.168.1.20:5173"
    assert phone.reachable_base("http://127.0.0.1:5173/") == "http://192.168.1.20:5173"
    assert phone.reachable_base("http://[::1]:5173") == "http://192.168.1.20:5173"
    assert phone.reachable_base("https://portal.example") == "https://portal.example"


def test_a_loopback_portal_stays_when_the_machine_has_no_lan_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(phone, "lan_address", lambda: None)
    assert phone.reachable_base("http://localhost:5173") == "http://localhost:5173"


def test_two_links_do_not_share_a_qr_code() -> None:
    one = phone.qr_svg("https://portal.test/i/one")
    two = phone.qr_svg("https://portal.test/i/two")
    assert one.startswith("<svg") and two.startswith("<svg")
    assert one != two
    # The image is the code. The URL is shown beside it, not written into the SVG.
    assert "https://portal.test" not in one
    assert "#fff" in one


def test_a_code_of_the_wrong_shape_is_refused_before_it_is_hashed() -> None:
    assert phone.acceptable_code("short") is False
    assert phone.acceptable_code("has a space") is False
    assert phone.acceptable_code("a" * 65) is False
    assert phone.acceptable_code("a" * 16) is True


@pytest.fixture(autouse=True)
def _no_phone_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own phone link base (a local https proxy) is not these tests' portal."""
    monkeypatch.setattr(settings, "identity_phone_base_url", "")


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """ComplyCube configured, and a master key to seal the client's photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    yield


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
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


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_portal_base_url", "https://portal.test")


def _choice(fx: dict[str, object]) -> IdentityChoice:
    return IdentityChoice(step_key="idv", case_uid=str(fx["case_uid"]))


async def _open(sess: AsyncSession, fx: dict[str, object]) -> api.IdentityPhoneLinkOut:
    return await api.create_my_identity_phone_link(body=_choice(fx), ctx=await _client(sess, fx))


async def _trail(sess: AsyncSession, tenant: int) -> str:
    """Everything this firm wrote to either trail, as text."""
    log = (
        await sess.execute(
            text("SELECT detail::text FROM audit.log WHERE tenant_id = :t"), {"t": tenant}
        )
    ).scalars()
    actions = (
        await sess.execute(
            text("SELECT metadata::text FROM audit.actions WHERE tenant_id = :t"), {"t": tenant}
        )
    ).scalars()
    return "\n".join([*(log.all()), *(actions.all())])


async def test_the_phone_routes_are_not_on_the_public_api() -> None:
    """No internal token is the same answer as a route that is not there: 404, and the
    wording is the token check's, so a missing mount cannot hide behind it."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/identity-phone/redeem", json={"code": "abcdefgh"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


async def test_a_link_is_opened_once_and_the_phone_can_send_the_photos(
    service_session: AsyncSession,
    complycube: None,
    bucket: dict[str, bytes],
    portal: None,
) -> None:
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    made = await _open(service_session, fx)
    code = made.url.removeprefix("https://portal.test/i/")

    assert made.url == f"https://portal.test/i/{code}"
    assert code not in made.qr_svg
    assert made.qr_svg.startswith("<svg")

    token = await phone.redeem(service_session, code)
    assert token is not None and token.startswith(phone.TOKEN_PREFIX)
    assert await phone.redeem(service_session, code) is None

    link = await phone.resolve(service_session, token)
    assert link is not None and link.step_key == "idv"
    await phone.publish_claims(service_session, link)

    # The phone sends both photos and names the document in one request, as the signed-in page
    # does: they are held sealed for the worker, never put in the firm's bucket.
    sent = await identity_phone.submit_identity_phone(
        document_type="passport",
        idv_document_front=UploadFile(filename="front.jpg", file=BytesIO(FRONT_JPEG)),
        idv_document_back=None,
        idv_selfie=UploadFile(filename="selfie.jpg", file=BytesIO(SELFIE_JPEG)),
        link=link,
        session=service_session,
    )
    assert sent["status"] == "submitted"
    assert (await _capture(service_session, fx))["status"] == "submitted"
    assert set(await _held_copies(service_session, fx)) == {"idv_document_front", "idv_selfie"}
    assert bucket == {}

    # Sent once: the link cannot send them again.
    with pytest.raises(HTTPException) as refused:
        await identity_phone.submit_identity_phone(
            document_type="passport",
            idv_document_front=UploadFile(filename="again.jpg", file=BytesIO(FRONT_JPEG)),
            idv_document_back=None,
            idv_selfie=UploadFile(filename="again.jpg", file=BytesIO(SELFIE_JPEG)),
            link=link,
            session=service_session,
        )
    assert refused.value.status_code == 409

    trail = await _trail(service_session, int(fx["tenant"]))
    assert code not in trail
    assert token not in trail
    assert made.url not in trail
    assert "code_hash" not in trail
    assert "token_hash" not in trail


async def test_a_new_link_retires_the_one_it_replaces(
    service_session: AsyncSession, complycube: None, portal: None
) -> None:
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    first = await _open(service_session, fx)
    token = await phone.redeem(service_session, first.url.rsplit("/", 1)[-1])
    assert token is not None

    second = await _open(service_session, fx)
    assert second.url != first.url
    assert await phone.resolve(service_session, token) is None
    assert await phone.redeem(service_session, first.url.rsplit("/", 1)[-1]) is None
    assert await phone.redeem(service_session, second.url.rsplit("/", 1)[-1]) is not None


async def test_the_computer_sees_each_photo_the_phone_takes(
    service_session: AsyncSession, complycube: None, portal: None
) -> None:
    """The phone says what it has taken as it goes, so the waiting computer can show each one
    arrive. Nothing of the photos is sent, and a new link starts afresh."""
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    made = await _open(service_session, fx)
    token = await phone.redeem(service_session, made.url.rsplit("/", 1)[-1])
    assert token is not None
    link = await phone.resolve(service_session, token)
    assert link is not None
    assert (await _capture(service_session, fx))["phone_progress"] == {
        "document_type": None,
        "taken": [],
    }

    said = await identity_phone.identity_phone_taken(
        body=identity_phone.TakenIn(
            keys=["idv_document_front", "idv_selfie", "not_a_photo"], document_type="passport"
        ),
        link=link,
        session=service_session,
    )
    assert said == {"ok": True}
    seen = await _capture(service_session, fx)
    assert seen["phone_progress"] == {
        "document_type": "passport",
        "taken": ["idv_document_front", "idv_selfie"],
    }
    # Taken is not sent: every photo is still open, and nothing is held.
    assert {item["status"] for item in seen["items"]} == {"open"}
    assert await _held_copies(service_session, fx) == {}

    # A new link starts the photos afresh.
    await _open(service_session, fx)
    assert (await _capture(service_session, fx))["phone_progress"]["taken"] == []


async def test_a_late_phone_report_never_undoes_the_send(
    service_session: AsyncSession, complycube: None, bucket: dict[str, bytes], portal: None
) -> None:
    """The phone reports in the background, so a report read while the client was still taking
    photos can land after they were sent. It changes nothing: the try stays sent, so the worker
    still sends it."""
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    made = await _open(service_session, fx)
    token = await phone.redeem(service_session, made.url.rsplit("/", 1)[-1])
    assert token is not None
    link = await phone.resolve(service_session, token)
    assert link is not None
    await phone.publish_claims(service_session, link)

    # The report's read, taken while the client was still taking the photos.
    _row, before = await identity_phone._step(service_session, link)
    stale = idv_capture.CaptureStep(
        tenant_id=link.tenant_id,
        case_id=link.case_id,
        step_key=link.step_key,
        contact_id=link.contact_id,
        state=before,
        manual=False,
    )
    await identity_phone.submit_identity_phone(
        document_type="passport",
        idv_document_front=UploadFile(filename="front.jpg", file=BytesIO(FRONT_JPEG)),
        idv_document_back=None,
        idv_selfie=UploadFile(filename="selfie.jpg", file=BytesIO(SELFIE_JPEG)),
        link=link,
        session=service_session,
    )

    for keys in (["idv_document_front", "idv_selfie"], []):
        await idv_capture.record_phone_progress(
            service_session, stale, keys=keys, document_type="passport"
        )
        after = await _capture(service_session, fx)
        assert after["status"] == "submitted"
        assert after["phone_progress"] == {"document_type": None, "taken": []}


async def test_an_expired_link_cannot_be_opened_or_used(
    service_session: AsyncSession, complycube: None, portal: None
) -> None:
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    made = await _open(service_session, fx)
    await service_session.execute(
        text(
            "UPDATE auth.identity_phone_links SET expires_at = now() - interval '1 minute' "
            " WHERE tenant_id = :t"
        ),
        {"t": fx["tenant"]},
    )
    assert await phone.redeem(service_session, made.url.rsplit("/", 1)[-1]) is None

    # A session already minted dies with the window too.
    fresh = await _open(service_session, fx)
    token = await phone.redeem(service_session, fresh.url.rsplit("/", 1)[-1])
    assert token is not None
    await service_session.execute(
        text(
            "UPDATE auth.identity_phone_links SET expires_at = now() - interval '1 minute' "
            " WHERE tenant_id = :t"
        ),
        {"t": fx["tenant"]},
    )
    assert await phone.resolve(service_session, token) is None


async def test_a_firm_with_no_portal_is_not_given_a_link(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "client_portal_base_url", "   ")
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    with pytest.raises(HTTPException) as refused:
        await _open(service_session, fx)
    assert refused.value.status_code == 409
    count = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.identity_phone_links WHERE tenant_id = :t"),
            {"t": fx["tenant"]},
        )
    ).scalar_one()
    assert count == 0


async def test_taking_the_photos_here_withdraws_the_link(
    service_session: AsyncSession, complycube: None, portal: None
) -> None:
    fx = await _seed(service_session)
    await _instantiate(service_session, fx, "complycube")
    made = await _open(service_session, fx)
    withdrawn = await api.cancel_my_identity_phone_link(
        body=_choice(fx), ctx=await _client(service_session, fx)
    )
    assert withdrawn == {"withdrawn": True}
    assert await phone.redeem(service_session, made.url.rsplit("/", 1)[-1]) is None
    again = await api.cancel_my_identity_phone_link(
        body=_choice(fx), ctx=await _client(service_session, fx)
    )
    assert again == {"withdrawn": False}
