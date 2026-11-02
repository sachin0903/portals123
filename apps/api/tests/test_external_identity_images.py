"""A firm's identity images, from upload to the operations console, against a real Postgres.

The firm uploads a selfie and both sides of a driving licence; the files go to object storage
(an in-memory stand-in here) and the rows hold their object keys. Submitting the identity check
records the provider's document and live photo with those keys, and the event consumer then stores
what the provider says without losing them. The console lists the images the provider holds, which
is the one view recorded, and reads each one from the provider when it is shown. The firm's own
document list never picks up the provider's copies.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import storage, tenant_storage
from app.auth import Principal
from app.complycube import decisions, images
from app.complycube.client import ScreeningClient
from app.routers import aml
from external.live import LiveStore
from external.models import CheckCreate, DocumentUpload, held_document
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    accept,
    as_json,
    drain,
    fetch,
    seed_bed,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 32
BACK = b"\xff\xd8\xff\xe0" + b"\x03" * 32
BUCKET = "t-identity-test"


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    seeded = await seed_bed(maker)
    yield seeded
    await seeded.cleanup()


@pytest_asyncio.fixture
async def operator(bed: Bed) -> AsyncIterator[str]:
    """A console operator's ``auth.users`` id: the images they are shown are recorded against it."""
    user_id = str(uuid.uuid4())
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO auth.users (id, tenant_id, email, workos_user_id) "
                "VALUES (:id, :t, :email, :w)"
            ),
            {
                "id": user_id,
                "t": bed.tenant_id,
                "email": f"ops-{bed.nonce}@identity.test",
                "w": f"wos_ops_{bed.nonce}",
            },
        )
    yield user_id
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})


@pytest.fixture
def objects(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], tuple[bytes, str]]:
    """Object storage, in memory: ``(bucket, key) -> (bytes, content type)``."""
    held: dict[tuple[str, str], tuple[bytes, str]] = {}

    async def put_document(
        key: str, data: bytes, content_type: str, *, bucket: str | None = None
    ) -> None:
        held[(str(bucket), key)] = (data, content_type)

    async def get_document(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        return held[(str(bucket), key)]

    async def document_bucket(session: Any, tenant_id: int | None = None) -> str:
        return BUCKET

    monkeypatch.setattr(storage, "put_document", put_document)
    monkeypatch.setattr(storage, "get_document", get_document)
    monkeypatch.setattr(tenant_storage, "document_bucket", document_bucket)
    return held


async def _client_uid(bed: Bed) -> str:
    rows = await fetch(
        bed.maker, "SELECT uid FROM compliance.clients WHERE id = :c", {"c": bed.client_pk}
    )
    return str(rows[0]["uid"])


async def _upload(bed: Bed, client_uid: str, content: bytes, **fields: Any) -> Any:
    upload = DocumentUpload(file=base64.b64encode(content).decode("ascii"), **fields)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await LiveStore(s).create_document(
            client_uid, held_document(upload), tenant_id=bed.tenant_id
        )


class _Provider:
    """The provider's create endpoints for one identity submission."""

    def __init__(self, bed: Bed) -> None:
        self.document_id = bed.ref("doc")
        self.photo_id = bed.ref("photo")
        self.check_id = bed.ref("idchk")
        self.uploads: dict[str, dict[str, Any]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else {}
        if path.startswith("/addresses"):
            return httpx.Response(200, json={"items": [{"id": "addr"}]})
        if path == "/documents":
            return httpx.Response(200, json={"id": self.document_id})
        if path.startswith(f"/documents/{self.document_id}/upload/"):
            self.uploads[path.rsplit("/", 1)[-1]] = body
            return httpx.Response(200, json={"id": "img"})
        if path == "/livePhotos":
            return httpx.Response(200, json={"id": self.photo_id})
        if path == "/checks":
            return httpx.Response(200, json={"id": self.check_id, "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})


class _Holding(FakeProvider):
    """The provider once the check is done: ``FakeProvider``, plus the image downloads it answers
    by path (``/documents/{id}/download/{side}``, ``/livePhotos/{id}/download``)."""

    def __init__(self, bed: Bed) -> None:
        super().__init__(bed)
        self.downloads: dict[str, bytes] = {}

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if "/download" not in path:
            return super()._handler(request)
        self.calls.append(f"{request.method} {path}")
        data = self.downloads.get(path)
        if data is None:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(
            200,
            json={"fileName": "image", "contentType": "image/jpg", "data": _b64(data)},
        )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def test_partner_identity_images_reach_the_console(
    bed: Bed,
    objects: dict[tuple[str, str], tuple[bytes, str]],
    operator: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_uid = await _client_uid(bed)
    selfie = await _upload(bed, client_uid, JPEG, type="selfie")
    front = await _upload(bed, client_uid, PNG, type="drivers_license", issuing_country="GB")
    back = await _upload(
        bed, client_uid, BACK, type="drivers_license", issuing_country="GB", side="back"
    )
    assert back.side == "back"

    stored = await fetch(
        bed.maker,
        "SELECT uid, side, object_key, body FROM compliance.documents "
        "WHERE client_id = :c ORDER BY id",
        {"c": bed.client_pk},
    )
    keys = {row["uid"]: row["object_key"] for row in stored}
    prefix = f"files/clients/{bed.contact_uid}/"
    assert keys == {
        selfie.uid: f"{prefix}{selfie.uid}/original.jpg",
        front.uid: f"{prefix}{front.uid}/original.png",
        back.uid: f"{prefix}{back.uid}/original.jpg",
    }
    assert [row["side"] for row in stored] == ["front", "front", "back"]
    for row in stored:
        body = row["body"] if isinstance(row["body"], dict) else json.loads(row["body"])
        assert "data" not in body
        assert body["bucket"] == BUCKET
    assert objects[(BUCKET, keys[front.uid])] == (PNG, "image/png")
    assert objects[(BUCKET, keys[back.uid])] == (BACK, "image/jpeg")

    provider = _Provider(bed)
    screening = ScreeningClient("test-key", transport=httpx.MockTransport(provider.handle))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        check = await LiveStore(s, screening=screening).create_check(
            client_uid, CheckCreate(kind="identity"), tenant_id=bed.tenant_id, already_bound=True
        )
    assert check.document_uids == [selfie.uid, front.uid, back.uid]
    assert provider.uploads["front"]["data"] == base64.b64encode(PNG).decode("ascii")
    assert provider.uploads["front"]["fileName"] == "drivers_license-front.png"
    assert provider.uploads["back"]["data"] == base64.b64encode(BACK).decode("ascii")
    assert provider.uploads["back"]["fileName"] == "drivers_license-back.jpg"

    # The provider says the check is done; the consumer stores its document and live photo.
    fake = _Holding(bed)
    fake.checks[provider.check_id] = {
        "id": provider.check_id,
        "clientId": bed.provider_client_id,
        "type": "identity_check",
        "status": "complete",
        "outcome": "clear",
        "documentId": provider.document_id,
        "livePhotoId": provider.photo_id,
    }
    fake.documents[provider.document_id] = {
        "id": provider.document_id,
        "type": "driving_license",
        "classification": "proof_of_identity",
        "issuingCountry": "GB",
        "images": [
            {"documentSide": "front", "contentType": "image/png"},
            {"documentSide": "back", "contentType": "image/jpg"},
        ],
    }
    fake.photos[provider.photo_id] = {
        "id": provider.photo_id,
        "performLivenessCheck": True,
        "contentType": "image/jpg",
    }
    await accept(
        bed.maker,
        {
            "id": f"evt_id_{bed.nonce}",
            "type": "check.completed",
            "payload": {"id": provider.check_id},
        },
    )
    result = await drain(bed.maker, fake)
    assert result is not None and result.applied >= 1

    mirrors = await fetch(
        bed.maker,
        "SELECT object_key, back_object_key, classification FROM compliance.documents "
        "WHERE provider_document_id = :d",
        {"d": provider.document_id},
    )
    assert mirrors == [
        {
            "object_key": keys[front.uid],
            "back_object_key": keys[back.uid],
            "classification": "proof_of_identity",
        }
    ]
    photos = await fetch(
        bed.maker,
        "SELECT object_key FROM compliance.live_photos WHERE provider_photo_id = :p",
        {"p": provider.photo_id},
    )
    assert photos == [{"object_key": keys[selfie.uid]}]

    # The console lists the three images the provider holds for the check, and reads each one from
    # the provider when it is shown: the provider's copies, not ours.
    fake.downloads[f"/documents/{provider.document_id}/download/front"] = PNG
    fake.downloads[f"/documents/{provider.document_id}/download/back"] = BACK
    fake.downloads[f"/livePhotos/{provider.photo_id}/download"] = JPEG
    monkeypatch.setattr(decisions, "require_client", fake.client)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ctx = aml.AmlContext(
            session=s,
            tenant_id=bed.tenant_id,
            targeted=False,
            foreign=False,
            principal=Principal(user_id="user_ops", permissions=["aml:read"]),
            user_id=operator,
            own_tenant_id=bed.tenant_id,
        )
        detail = await aml.get_check(check.uid, ctx=ctx)
        served = {
            part: await aml.get_check_image(check.uid, part, ctx=ctx) for part in images.PARTS
        }
    identity = cast(dict[str, Any], detail["identity"])
    assert identity["images"] == [
        {"part": "front", "label": "ID front", "content_type": "image/png", "source": "complycube"},
        {"part": "back", "label": "ID back", "content_type": "image/jpeg", "source": "complycube"},
        {"part": "selfie", "label": "Selfie", "content_type": "image/jpeg", "source": "complycube"},
    ]
    assert {part: image.body for part, image in served.items()} == {
        "front": PNG,
        "back": BACK,
        "selfie": JPEG,
    }
    assert {image.media_type for image in served.values()} == {"image/png", "image/jpeg"}
    # Showing the check with its three images is the one view recorded, against the operator.
    # Serving each image records nothing more.
    views = await fetch(
        bed.maker,
        "SELECT route, actor_id::text AS actor, metadata -> 'subject' AS subject "
        "FROM audit.actions "
        "WHERE event_type = 'aml.identity_images_viewed' AND path_params ->> 'uid' = :u "
        "ORDER BY id",
        {"u": check.uid},
    )
    assert [(view["route"], view["actor"], as_json(view["subject"])) for view in views] == [
        ("/aml/checks/{uid}", operator, {"image_count": 3})
    ]

    # A second check still sees only the firm's three uploads, not the provider's copy.
    second = _Provider(bed)
    second.check_id = bed.ref("idchk2")
    screening = ScreeningClient("test-key", transport=httpx.MockTransport(second.handle))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        again = await LiveStore(s, screening=screening).create_check(
            client_uid, CheckCreate(kind="identity"), tenant_id=bed.tenant_id, already_bound=True
        )
    assert again.document_uids == [selfie.uid, front.uid, back.uid]
