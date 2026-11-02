"""A client's signed W-9 or W-8 (0261): the upload, its refusals, and the rule that the file
belongs to the answer it was filed for.

The upload runs `POST /crm/contacts/{uid}/fatca-document`'s handler against a real service_role
session (as `firm_book_write_session` gives it) with object storage faked. The clear-on-change rule
is a trigger, so it is driven through BOTH writers of `fatca_status`: the client record's PATCH and
a flow's form write-back. The migration's seed is run as SQL against a real Postgres, because
`documents.document_types` is FORCE RLS and a seed that runs as the wrong role inserts nothing and
still exits 0.
"""

from __future__ import annotations

import importlib.util
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
from app.flows.step_handlers import _write_contact_patch
from app.routers import crm as crm_router
from app.routers import firm as firm_router
from tests.conftest import assert_public_id

_ROOT = Path(__file__).resolve().parent.parent / "migrations"
# Escaped the way `upgrade()` escapes it: the driver reads `%` as a placeholder.
_SQL = (
    (_ROOT / "sql/0261_contact_fatca_document.sql").read_text(encoding="utf-8").replace("%", "%%")
)

_spec = importlib.util.spec_from_file_location(
    "m0261", _ROOT / "versions/0261_contact_fatca_document.py"
)
assert _spec is not None and _spec.loader is not None
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _file(data: bytes = _PDF, name: str = "scan.pdf") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(data), size=len(data))


def _ctx(session: AsyncSession, tenant: int, *perms: str) -> FirmContext:
    # role='adviser' so require_permission enforces `perms` rather than waving a system caller in.
    return FirmContext(
        session=session,
        tenant_id=tenant,
        principal=Principal(
            user_id="wos_staff",
            role="adviser",
            permissions=list(perms) if perms else ["contacts:write"],
        ),
    )


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


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _tenant(sess: AsyncSession) -> int:
    s = uuid.uuid4().hex[:10]
    return int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) "
            "VALUES (:u, :a, 'FATCA firm') RETURNING id",
            u=f"ten_{s}",
            a=f"fatca-{s}",
        )
    )


async def _contact(
    sess: AsyncSession, tenant: int, *, us_person: bool | None, fatca_status: str | None
) -> int:
    return int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, us_person, fatca_status) "
            "VALUES (:t, :u, 'individual', :us, :fs) RETURNING id",
            t=tenant,
            u=uuid.uuid4().hex[:12],
            us=us_person,
            fs=fatca_status,
        )
    )


async def _linked(sess: AsyncSession, contact: int) -> str | None:
    """The uid of the document the contact's FATCA answer points at, or None."""
    return await _scalar(
        sess,
        "SELECT d.uid FROM crm.contacts c JOIN documents.documents d "
        "  ON d.id = c.fatca_document_id WHERE c.id = :c",
        c=contact,
    )


async def _upload(
    sess: AsyncSession, tenant: int, contact: int, file: UploadFile | None = None
) -> crm_router.FatcaDocumentUploaded:
    return await crm_router.upload_contact_fatca_document(
        contact_id=contact, file=file or _file(), ctx=_ctx(sess, tenant)
    )


# ---- the upload --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signed_w9_is_filed_on_the_client_and_linked_to_the_answer(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    written = _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=True, fatca_status="w9")

    out = await _upload(sess, tenant, contact)

    assert out.fatca_document.name == "W-9"
    assert_public_id(out.fatca_document.uid)
    assert await _linked(sess, contact) == out.fatca_document.uid
    assert len(written) == 1
    document = (
        (
            await sess.execute(
                text(
                    "SELECT d.document_type, d.name, d.source, f.content_type "
                    "FROM documents.documents d "
                    "JOIN documents.files f ON f.id = d.original_file_id "
                    "WHERE d.uid = :u"
                ),
                {"u": out.fatca_document.uid},
            )
        )
        .mappings()
        .one()
    )
    assert dict(document) == {
        "document_type": "fatca_form",
        "name": "W-9",
        "source": "uploaded",
        "content_type": "application/pdf",
    }
    # The client's document, on their record — the same owner link every evidence upload makes.
    assert await _scalar(
        sess,
        "SELECT 1 FROM documents.document_contacts dc JOIN documents.documents d "
        "  ON d.id = dc.document_id WHERE d.uid = :u AND dc.contact_id = :c AND dc.role = 'owner'",
        u=out.fatca_document.uid,
        c=contact,
    )
    # The document's own trail says it was uploaded, and for which answer.
    detail = await _scalar(
        sess,
        "SELECT al.detail FROM documents.audit_log al JOIN documents.documents d "
        "  ON d.id = al.document_id WHERE d.uid = :u AND al.action = 'document.uploaded'",
        u=out.fatca_document.uid,
    )
    assert detail["fatca_status"] == "w9"
    assert detail["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_a_photographed_w8_is_named_w8_and_replaces_the_earlier_link(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=False, fatca_status="w8")

    first = await _upload(sess, tenant, contact)
    second = await _upload(sess, tenant, contact, _file(_PNG, "photo.png"))

    assert second.fatca_document.name == "W-8"
    assert await _linked(sess, contact) == second.fatca_document.uid
    # The earlier copy is not deleted; it is simply no longer the one the answer points at.
    assert await _scalar(
        sess, "SELECT 1 FROM documents.documents WHERE uid = :u", u=first.fatca_document.uid
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [None, "not_certified", "w8_cure"])
async def test_no_w9_or_w8_answer_is_refused_with_409(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, held: str | None
) -> None:
    sess = service_session
    written = _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=None, fatca_status=held)

    with pytest.raises(HTTPException) as exc:
        await _upload(sess, tenant, contact)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "Choose W-9 or W-8 for this client before you upload the signed form."
    )
    assert written == []
    assert await _linked(sess, contact) is None


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_pdf_or_an_image_is_refused(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store itself takes plain text; a signed form is a scan or a photograph."""
    sess = service_session
    written = _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=True, fatca_status="w9")

    with pytest.raises(HTTPException) as exc:
        await _upload(sess, tenant, contact, _file(b"the client signed it", "w9.txt"))

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "The signed copy needs to be a PDF or an image (a scan or a photograph)."
    )
    assert written == []
    assert await _linked(sess, contact) is None


@pytest.mark.asyncio
async def test_the_upload_needs_contacts_write(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    written = _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=True, fatca_status="w9")

    with pytest.raises(HTTPException) as exc:
        await crm_router.upload_contact_fatca_document(
            contact_id=contact,
            file=_file(),
            ctx=_ctx(sess, tenant, "contacts:read", "documents:write"),
        )

    assert exc.value.status_code == 403
    assert written == []
    assert await _linked(sess, contact) is None


@pytest.mark.asyncio
async def test_another_firms_client_is_not_found(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write session is service_role, so the tenant predicate is the whole scope."""
    sess = service_session
    written = _fake_document_store(monkeypatch)
    theirs = await _contact(sess, await _tenant(sess), us_person=True, fatca_status="w9")

    with pytest.raises(HTTPException) as exc:
        await _upload(sess, await _tenant(sess), theirs)

    assert exc.value.status_code == 404
    assert written == []


# ---- the file belongs to the answer it was filed for ---------------------------------------------


@pytest.mark.asyncio
async def test_the_client_record_patch_unlinks_the_form_when_the_answer_changes(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=True, fatca_status="w9")
    filed = await _upload(sess, tenant, contact)

    async def patch(body: dict[str, object]) -> None:
        await firm_router.update_contact(
            contact_uid=str(contact),
            contact_id=contact,
            body=firm_router.UpdateContact.model_validate(body),
            ctx=_ctx(sess, tenant),
        )

    # The same answer saved again is not a change.
    await patch({"fatca_status": "w9", "first_name": "Sam"})
    assert await _linked(sess, contact) == filed.fatca_document.uid

    await patch({"us_person": False, "fatca_status": "w8"})
    assert await _linked(sess, contact) is None
    # The document stays in the client's documents.
    assert await _scalar(
        sess,
        "SELECT 1 FROM documents.document_contacts dc JOIN documents.documents d "
        "  ON d.id = dc.document_id WHERE d.uid = :u AND dc.contact_id = :c",
        u=filed.fatca_document.uid,
        c=contact,
    )

    # Clearing the answer clears the link too.
    refiled = await _upload(sess, tenant, contact)
    assert await _linked(sess, contact) == refiled.fatca_document.uid
    await patch({"fatca_status": None})
    assert await _linked(sess, contact) is None


@pytest.mark.asyncio
async def test_a_flow_write_back_unlinks_the_form_when_the_answer_changes(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=False, fatca_status="w8")
    filed = await _upload(sess, tenant, contact)

    # The fact-find answered again with the same W-8 keeps the form.
    await _write_contact_patch(sess, tenant, contact, {"us_person": "No", "fatca_status": "W8"})
    assert await _linked(sess, contact) == filed.fatca_document.uid

    await _write_contact_patch(sess, tenant, contact, {"us_person": "Yes", "fatca_status": "w9"})
    assert await _linked(sess, contact) is None
    assert await _scalar(
        sess, "SELECT 1 FROM documents.documents WHERE uid = :u", u=filed.fatca_document.uid
    )


@pytest.mark.asyncio
async def test_a_new_answer_and_its_form_written_together_keep_the_form(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trigger clears only a link the statement did not set itself."""
    sess = service_session
    _fake_document_store(monkeypatch)
    tenant = await _tenant(sess)
    contact = await _contact(sess, tenant, us_person=True, fatca_status="w9")
    await _upload(sess, tenant, contact)
    other = await _contact(sess, tenant, us_person=False, fatca_status="w8")
    w8 = await _upload(sess, tenant, other)

    await sess.execute(
        text(
            "UPDATE crm.contacts SET us_person = false, fatca_status = 'w8', "
            "  fatca_document_id = (SELECT id FROM documents.documents WHERE uid = :u) "
            "WHERE id = :c"
        ),
        {"u": w8.fatca_document.uid, "c": contact},
    )
    assert await _linked(sess, contact) == w8.fatca_document.uid


# ---- the migration -------------------------------------------------------------------------------


def _firm(conn: Any, prefix: str) -> int:
    s = uuid.uuid4().hex[:10]
    return int(
        conn.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, 'Seeded') RETURNING id"
            ),
            {"u": f"ten_{prefix}{s}", "a": f"{prefix}-{s}"},
        ).scalar_one()
    )


def _fatca_types(conn: Any, tenants: list[int]) -> list[tuple[int, str, int]]:
    rows = conn.execute(
        text(
            "SELECT tenant_id, label, sort_order FROM documents.document_types "
            "WHERE kind = 'document' AND slug = 'fatca_form' AND tenant_id = ANY(:t) "
            "ORDER BY tenant_id"
        ),
        {"t": tenants},
    )
    return [(int(r.tenant_id), str(r.label), int(r.sort_order)) for r in rows]


def test_a_new_firm_starts_with_the_w9_w8_type(super_engine: Engine) -> None:
    with super_engine.connect() as conn:
        firm = _firm(conn, "fnew")
        assert _fatca_types(conn, [firm]) == [(firm, "W-9 / W-8", 10)]


def test_the_migration_seeds_every_existing_firm_once_and_keeps_a_renamed_label(
    db: dict[str, str], super_engine: Engine
) -> None:
    with super_engine.connect() as conn:
        # A firm from before 0261 has no row; a firm that renamed the type keeps its own label.
        missing = _firm(conn, "fmiss")
        conn.execute(
            text(
                "DELETE FROM documents.document_types "
                "WHERE tenant_id = :t AND kind = 'document' AND slug = 'fatca_form'"
            ),
            {"t": missing},
        )
        renamed = _firm(conn, "fren")
        conn.execute(
            text(
                "UPDATE documents.document_types SET label = 'FATCA certificate' "
                "WHERE tenant_id = :t AND kind = 'document' AND slug = 'fatca_form'"
            ),
            {"t": renamed},
        )
    migrator = create_engine(db["migrator"])
    try:
        with migrator.connect() as conn:
            # Twice: every statement in the file can run again.
            conn.exec_driver_sql(_SQL)
            conn.exec_driver_sql(_SQL)
            conn.exec_driver_sql("SET LOCAL ROLE service_role")
            seeded = _fatca_types(conn, [missing, renamed])
            conn.rollback()
    finally:
        migrator.dispose()
    assert seeded == [(missing, "W-9 / W-8", 10), (renamed, "FATCA certificate", 10)]


def test_the_downgrade_drops_the_link_and_keeps_a_type_a_document_uses(
    db: dict[str, str], super_engine: Engine
) -> None:
    with super_engine.connect() as conn:
        used = _firm(conn, "fused")
        unused = _firm(conn, "funused")
        conn.execute(
            text(
                "INSERT INTO documents.documents (uid, tenant_id, document_type, source, name, "
                "  status) VALUES (:u, :t, 'fatca_form', 'uploaded', 'W-9', 'generated')"
            ),
            {"u": uuid.uuid4().hex[:12], "t": used},
        )
    migrator = create_engine(db["migrator"])
    try:
        with migrator.connect() as conn:
            conn.exec_driver_sql(_migration.DOWN)
            column = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = 'crm' "
                    "  AND table_name = 'contacts' AND column_name = 'fatca_document_id'"
                )
            ).scalar_one_or_none()
            trigger = conn.execute(
                text(
                    "SELECT 1 FROM pg_trigger "
                    "WHERE tgname = 'contacts_fatca_document_follows_status'"
                )
            ).scalar_one_or_none()
            conn.exec_driver_sql("SET LOCAL ROLE service_role")
            kept = _fatca_types(conn, [used, unused])
            later = _firm(conn, "flater")
            later_types = _fatca_types(conn, [later])
            conn.rollback()
    finally:
        migrator.dispose()
    assert column is None
    assert trigger is None
    assert [tenant for tenant, _, _ in kept] == [used]
    assert later_types == []
