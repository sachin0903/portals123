"""Behaviour tests for the files entity (documents.attachments): upload / list / get / url / delete.

Files are the supporting-upload counterpart to documents — separate table, separate router, but the
same blob layer and the same tenant/contact RLS. Object storage is mocked (no S3 in tests), so the
classification, contact binding, RLS scoping and soft-delete are exercised against a real Postgres.
"""

import io
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers

from app import storage
from app.auth import Principal
from app.routers import files as files_router
from tests.conftest import assert_public_id


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, str]:
    """A tenant + staff user + a client contact, and a second tenant's contact for isolation."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"ften_{s}", "alias": f"ffirm-{s}", "name": "FFirm"},
            ).scalar_one()
        )
        staff_user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Fi', 'Staff', true) RETURNING id"
                ),
                {"uid": f"fusr_{s}", "email": f"staff_{s}@f.test", "wos": f"fwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": staff_user, "t": tenant},
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', 'Fee Client', :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"fcnt_{s}", "email": f"client_{s}@f.test"},
            ).scalar_one()
        )
        other_tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"ften2_{s}", "alias": f"ffirm2-{s}", "name": "F2"},
            ).scalar_one()
        )
        foreign_contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', 'Other', :email) RETURNING id"
                ),
                {"t": other_tenant, "uid": f"fcnt2_{s}", "email": f"other_{s}@f.test"},
            ).scalar_one()
        )
        c.commit()

    return {
        "tenant_id": str(tenant),
        "user_uuid": staff_user,
        "org": f"forg_{s}",
        "wos": f"fwos_{s}",
        "contact_id": str(contact),
        "foreign_contact_id": str(foreign_contact),
    }


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


def _upload(name: str = "passport.pdf", data: bytes = b"%PDF-1.4 x") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=name,
        size=len(data),
        headers=Headers({"content-type": "application/pdf"}),
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], seed: dict[str, str], monkeypatch):
    # Storage is mocked: validate_upload accepts, put_document no-ops, presigned returns a stub.
    monkeypatch.setattr(
        files_router.storage,
        "validate_upload",
        lambda data, declared=None: ("application/pdf", "pdf"),
    )

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    async def _presigned(
        key: str,
        expires: int = 900,
        *,
        filename: str | None = None,
        disposition: str = "attachment",
        bucket: str | None = None,
    ) -> str:
        return f"https://signed.example/{key}"

    monkeypatch.setattr(files_router.storage, "put_document", _put)
    monkeypatch.setattr(files_router.storage, "presigned_get_document", _presigned)

    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": seed["wos"],
        "role": "adviser",
        "portal": "platform",
        # contacts:read_all lets the staff session SEE the contact under RLS (so contact binding
        # validates); documents:* gate the files endpoints.
        "permissions": ["documents:read", "documents:write", "contacts:read_all"],
        "org_id": seed["org"],
        "user_uuid": seed["user_uuid"],
        "tenant_id": int(seed["tenant_id"]),
        "tenant_ids": [int(seed["tenant_id"])],
        "contact_ids": [],
        "account_ids": [],
    }

    @asynccontextmanager
    async def _open() -> AsyncIterator:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE authenticated"))
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            yield session

    yield _open
    await engine.dispose()


async def _upload_one(session_factory, seed, **kw):
    async with session_factory() as session:
        return await files_router.upload_file(
            file=kw.get("file", _upload()),
            attachment_type=kw.get("attachment_type", "passport"),
            contact_id=kw.get("contact_id", int(seed["contact_id"])),
            name=kw.get("name"),
            principal=_principal(["documents:write"]),
            session=session,
        )


@pytest.mark.asyncio
async def test_upload_list_get(session_factory, seed) -> None:
    out = await _upload_one(session_factory, seed, name="Passport scan")
    assert_public_id(out.uid)
    assert out.attachment_type == "passport"
    assert out.contact_id == int(seed["contact_id"])
    assert out.name == "Passport scan"

    async with session_factory() as session:
        listed = await files_router.list_files(
            principal=_principal(["documents:read"]), session=session
        )
        scoped = await files_router.list_files(
            contact_id=int(seed["contact_id"]),
            principal=_principal(["documents:read"]),
            session=session,
        )
        got = await files_router.get_file(
            uid=out.uid, principal=_principal(["documents:read"]), session=session
        )
    assert out.uid in {f.uid for f in listed}
    assert out.uid in {f.uid for f in scoped}
    assert got.uid == out.uid


@pytest.mark.asyncio
async def test_a_read_targets_the_bucket_the_write_used(session_factory, seed, monkeypatch) -> None:
    """What was written to the tenant's bucket must be read back from the tenant's bucket.

    Without this the half-migrated state is invisible: a tenant key carries no tenant segment, so
    a read pointed at the shared bucket 404s, and a 404 reads as "the file isn't there" rather
    than as the wrong bucket. `file_content` had no coverage at all before this.
    """
    wrote: list[str | None] = []
    read: list[str | None] = []

    async def _put(key: str, data: bytes, content_type: str, *, bucket: str | None = None) -> None:
        wrote.append(bucket)

    async def _get(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        read.append(bucket)
        return b"%PDF-1.7", "application/pdf"

    async def _tenant_bucket(session, tenant_id=None) -> str:
        return "tnt-thisfirm"

    monkeypatch.setattr(files_router.storage, "put_document", _put)
    monkeypatch.setattr(files_router.storage, "get_document", _get)
    monkeypatch.setattr(files_router.tenant_storage, "document_bucket", _tenant_bucket)

    out = await _upload_one(session_factory, seed)
    async with session_factory() as session:
        resp = await files_router.file_content(
            uid=out.uid, principal=_principal(["documents:read"]), session=session
        )
    assert resp.status_code == 200
    assert wrote == ["tnt-thisfirm"], "the write must land in the tenant's own bucket"
    assert read == wrote, "the read must target the bucket the row records, not the shared one"


async def test_file_url_and_delete(session_factory, seed) -> None:
    out = await _upload_one(session_factory, seed)
    async with session_factory() as session:
        url = await files_router.file_url(
            uid=out.uid, principal=_principal(["documents:read"]), session=session
        )
        assert url["url"].startswith("https://signed.example/")
        resp = await files_router.delete_file(
            uid=out.uid, principal=_principal(["documents:write"]), session=session
        )
        assert resp.status_code == 204
        listed = await files_router.list_files(
            principal=_principal(["documents:read"]), session=session
        )
    assert out.uid not in {f.uid for f in listed}  # soft-deleted → hidden


@pytest.mark.asyncio
async def test_upload_requires_write(session_factory, seed) -> None:
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await files_router.upload_file(
                file=_upload(),
                attachment_type="passport",
                contact_id=None,
                name=None,
                principal=_principal(["documents:read"]),  # no write
                session=session,
            )
    assert exc.value.status_code == 403


class _OverCapPart:
    """A file part that declares NO size (a chunked POST with no Content-Length) and then yields
    more bytes than the cap allows. Bytes are made per read, so nothing is allocated up front."""

    def __init__(self, total: int) -> None:
        self.filename = "huge.pdf"
        self.content_type = "application/pdf"
        self.size: int | None = None
        self._left = total

    async def read(self, size: int = -1, /) -> bytes:
        if self._left <= 0:
            return b""
        take = self._left if size < 0 else min(size, self._left)
        self._left -= take
        return (b"%PDF-1.4" + b"x" * (take - 8)) if take > 8 else b"x" * take


@pytest.mark.asyncio
async def test_an_undeclared_over_cap_upload_413s() -> None:
    """A supporting-file upload that sends no Content-Length used to be buffered whole before the
    size check could see it. It is now refused while streaming — and before the session is touched,
    which is why this one needs no fixtures (session=None is never used)."""
    with pytest.raises(HTTPException) as exc:
        await files_router.upload_file(
            file=_OverCapPart(storage.MAX_DOCUMENT_BYTES + 1),  # type: ignore[arg-type]
            attachment_type="passport",
            contact_id=None,
            name=None,
            principal=_principal(["documents:write"]),
            session=None,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 413
    assert "MiB" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_invalid_attachment_type_422(session_factory, seed) -> None:
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await files_router.upload_file(
                file=_upload(),
                attachment_type="not_a_type",
                contact_id=None,
                name=None,
                principal=_principal(["documents:write"]),
                session=session,
            )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_cannot_bind_foreign_contact_422(session_factory, seed) -> None:
    """A contact in another tenant is invisible under RLS, so binding a file to it 422s."""
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await files_router.upload_file(
                file=_upload(),
                attachment_type="passport",
                contact_id=int(seed["foreign_contact_id"]),
                name=None,
                principal=_principal(["documents:write"]),
                session=session,
            )
    assert exc.value.status_code == 422
