"""A custodian document filed by the pipeline (migration 0182).

``book_writer`` must be able to call the function and still be unable to read
``documents.documents``. The function itself is idempotent, files the document
against the client, and marks it as one the firm may not delete.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _tenant(session: AsyncSession) -> int:
    uid = f"ten_{uuid.uuid4().hex[:12]}"
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :u) RETURNING id"
                ),
                {"u": uid},
            )
        ).scalar_one()
    )


async def _contact(session: AsyncSession, tenant_id: int) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, :u, 'individual') RETURNING id"
                ),
                {"t": tenant_id, "u": f"con_{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
    )


def _args(
    tenant_id: int, contact_id: int, external_id: str, created: str | None = None
) -> dict[str, object]:
    meta: dict[str, object] = {
        "provider": "seccl",
        "external_id": external_id,
        "client_id": "C1",
    }
    if created is not None:
        meta["created"] = created
    return {
        "tenant": tenant_id,
        "contact": contact_id,
        "bucket": "t-test",
        "key": f"files/provider/seccl/contractnote/C1/{external_id}-note.pdf",
        "external": external_id,
        "meta": json.dumps(meta),
    }


async def _ingest(session: AsyncSession, args: dict[str, object]) -> str:
    return str(
        (
            await session.execute(
                text(
                    "SELECT documents.ingest_provider_document("
                    "  :tenant, :contact, :bucket, :key, 'application/pdf', 12, "
                    "  'abc', 'contract_note', 'Contract note', 'note.pdf', "
                    "  'seccl', :external, CAST(:meta AS jsonb))"
                ),
                args,
            )
        ).scalar_one()
    )


async def test_ingest_files_once_and_is_idempotent(service_session: AsyncSession) -> None:
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    external = uuid.uuid4().hex
    args = _args(tenant_id, contact_id, external)

    first = await _ingest(service_session, args)
    second = await _ingest(service_session, args)
    assert first == second

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT d.source, d.deletable, d.status::text AS status, "
                    "       d.intended_contact_id, f.bucket, f.storage_key, dc.role "
                    "  FROM documents.documents d "
                    "  JOIN documents.files f ON f.id = d.pdf_file_id "
                    "  JOIN documents.document_contacts dc ON dc.document_id = d.id "
                    " WHERE d.uid = :uid"
                ),
                {"uid": first},
            )
        )
        .mappings()
        .one()
    )
    assert row["source"] == "provider"
    assert row["deletable"] is False
    assert row["status"] == "generated"
    assert row["intended_contact_id"] == contact_id
    assert row["bucket"] == "t-test"
    assert row["role"] == "subject"
    assert str(row["storage_key"]).startswith("files/provider/seccl/")

    files = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM documents.files f "
                "  JOIN documents.documents d ON d.pdf_file_id = f.id "
                " WHERE d.uid = :uid"
            ),
            {"uid": first},
        )
    ).scalar_one()
    assert files == 1


async def test_a_repeat_moves_the_file_off_the_doubled_name(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    external = "0000FLMHS"
    doubled = _args(tenant_id, contact_id, external)
    doubled["key"] = "files/provider/seccl/contractnote/03235JB/0000FLMHS-0000FLMHS.pdf"
    single = _args(tenant_id, contact_id, external)
    single["key"] = "files/provider/seccl/contractnote/03235JB/0000FLMHS.pdf"

    first = await _ingest(service_session, doubled)
    again = await _ingest(service_session, single)
    assert again == first

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT f.storage_key, "
                    "       (SELECT count(*) FROM documents.files WHERE tenant_id = :t) AS files "
                    "  FROM documents.documents d "
                    "  JOIN documents.files f ON f.id = d.pdf_file_id "
                    " WHERE d.uid = :uid"
                ),
                {"uid": first, "t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["storage_key"] == single["key"]
    assert row["files"] == 1


async def test_a_message_date_replaces_the_copy_time_and_a_later_call_keeps_it(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    external = uuid.uuid4().hex
    message_time = datetime(2024, 11, 3, 8, 50, 30, 349000, tzinfo=UTC)

    uid = await _ingest(service_session, _args(tenant_id, contact_id, external))
    moved = await _ingest(
        service_session,
        _args(tenant_id, contact_id, external, created="2024-11-03T08:50:30.349Z"),
    )
    assert moved == uid
    # A provider with no message clock must not stamp now() over a date already kept.
    again = await _ingest(service_session, _args(tenant_id, contact_id, external))
    assert again == uid

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT d.created_at, d.generated_at, f.created_at AS file_created "
                    "  FROM documents.documents d "
                    "  JOIN documents.files f ON f.id = d.pdf_file_id "
                    " WHERE d.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one()
    )
    assert row["created_at"] == message_time
    assert row["generated_at"] == message_time
    assert row["file_created"] == message_time


async def test_an_unparseable_message_date_does_not_fail_the_filing(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    external = uuid.uuid4().hex
    uid = await _ingest(
        service_session, _args(tenant_id, contact_id, external, created="not-a-date")
    )
    created_at = (
        await service_session.execute(
            text("SELECT created_at FROM documents.documents WHERE uid = :uid"),
            {"uid": uid},
        )
    ).scalar_one()
    assert created_at is not None


async def test_a_firm_document_gains_a_subject_when_a_client_is_named_later(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    external = uuid.uuid4().hex
    firm = _args(tenant_id, contact_id, external)
    firm["contact"] = None

    uid = await _ingest(service_session, firm)
    links = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM documents.document_contacts dc "
                "  JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :uid"
            ),
            {"uid": uid},
        )
    ).scalar_one()
    assert links == 0

    again = await _ingest(service_session, _args(tenant_id, contact_id, external))
    assert again == uid
    role = (
        await service_session.execute(
            text(
                "SELECT dc.role FROM documents.document_contacts dc "
                "  JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :uid"
            ),
            {"uid": uid},
        )
    ).scalar_one()
    assert role == "subject"


async def test_book_writer_can_call_the_function_and_cannot_read_documents(
    service_session: AsyncSession,
) -> None:
    execute = (
        await service_session.execute(
            text(
                "SELECT has_function_privilege('book_writer', "
                "'documents.ingest_provider_document("
                "int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb"
                ")', 'EXECUTE')"
            )
        )
    ).scalar_one()
    select = (
        await service_session.execute(
            text("SELECT has_table_privilege('book_writer', 'documents.documents', 'SELECT')")
        )
    ).scalar_one()
    assert execute is True
    assert select is False
