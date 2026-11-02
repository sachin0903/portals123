"""A document can be marked as one the firm may NOT delete (migration 0122).

The case this exists for is provider ingest: a document synced from a custodian is that
custodian's record, and a firm archiving it would silently diverge our copy of the book from
theirs. The pipeline files those through ``documents.ingest_provider_document`` (0182), which
sets the flag. These tests hold the archive contract the flag depends on.

Two halves, and both matter: the flag has to REACH THE WIRE (or the UI cannot withhold a Delete
button it knows will fail), and the archive endpoint has to REFUSE (or withholding the button is
the only protection, which a curl request walks straight past).
"""

# ruff: noqa: F811 -- pytest fixtures are REQUESTED by parameter name, so importing `firm` /
# `session_factory` from the documents suite and then naming them in a signature reads to ruff
# as a redefinition. It is how fixture reuse works; the alternative is a third copy of a tenant
# fixture that already exists twice.

import inspect
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text

from app import storage
from app.auth import Principal
from app.routers import documents as docs

# Reuses the documents suite's own tenant fixtures rather than restating them: `firm` builds the
# tenant, staff and contacts, and `session_factory` binds an RLS session to that staff member.
from .test_documents_api import (  # noqa: F401 - fixtures are used by injection
    PDF,
    firm,
    session_factory,
)

pytestmark = pytest.mark.anyio


def _principal() -> Principal:
    return Principal(
        user_id="x",
        organisation_id="x",
        role="adviser",
        permissions=["documents:write", "documents:send", "contacts:read_all"],
    )


async def _upload(session_factory, monkeypatch, *, deletable: bool) -> docs.DocumentOut:
    """Store one document, deletable or not.

    Goes through ``_store_upload``, NOT the HTTP endpoint, and that is the contract being
    exercised: ``deletable`` is deliberately not a form field, because nothing sets it back to true
    and a caller who could post ``deletable=false`` could mint a document only a hand-written SQL
    UPDATE can ever remove. A provider-ingest writer reaches this keyword; an uploading human
    cannot.
    """

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
        doc = await docs._store_upload(
            session,
            tenant_id=tenant_id,
            file=UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF)),
            data=PDF,
            document_type="statement",
            name=None,
            deletable=deletable,
        )
        await session.commit()
    return doc


async def test_the_upload_endpoint_takes_no_deletable_field(
    session_factory, firm, monkeypatch
) -> None:
    """A human upload is ALWAYS deletable, and no form field says otherwise.

    This is the guard, not a formality: ``deletable`` has no PATCH field and no endpoint that
    resets it, so exposing it on the upload form would hand every ``documents:write`` caller a
    one-way door to a document nobody can remove through the product. Pinned on the SIGNATURE,
    because that is what FastAPI turns into the wire contract.
    """
    assert "deletable" not in inspect.signature(docs.upload_document).parameters

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
    assert result.documents[0].deletable is True


async def test_an_ordinary_upload_is_deletable_and_archives(
    session_factory,
    firm,
    monkeypatch,
) -> None:
    """The default is unchanged behaviour: nothing the portal sends carries the flag, so every
    human upload stays deletable and archive works exactly as it did."""
    doc = await _upload(session_factory, monkeypatch, deletable=True)
    assert doc.deletable is True

    async with session_factory() as session:
        archived = await docs.archive_document(uid=doc.uid, principal=_principal(), session=session)
    assert archived.status == "archived"


async def test_an_undeletable_document_is_refused_and_stays_live(
    session_factory,
    firm,
    monkeypatch,
) -> None:
    """409, not 404 and not a silent success. The endpoint treats an ALREADY-archived document as
    success (the bulk bar retries), so folding this into the UPDATE's WHERE would have reported a
    refusal as a completed delete — the one outcome that must never happen here."""
    doc = await _upload(session_factory, monkeypatch, deletable=False)
    assert doc.deletable is False

    with pytest.raises(HTTPException) as exc:
        async with session_factory() as session:
            await docs.archive_document(uid=doc.uid, principal=_principal(), session=session)
    assert exc.value.status_code == 409
    assert "cannot be deleted" in str(exc.value.detail)

    # …and it is genuinely untouched, not merely reported as such.
    async with session_factory() as session:
        after = await docs.get_document(uid=doc.uid, principal=_principal(), session=session)
    assert after.status == "generated"


async def test_the_flag_reaches_the_wire_on_every_read(
    session_factory,
    firm,
    monkeypatch,
) -> None:
    """Upload, single read and listing all carry it. The UI hides Delete and excludes the row from
    the bulk bar off this field, so a read that dropped it would silently offer the button back —
    the `client_contact_id` failure mode, which is why this asserts the endpoint's own output."""
    doc = await _upload(session_factory, monkeypatch, deletable=False)

    async with session_factory() as session:
        fetched = await docs.get_document(uid=doc.uid, principal=_principal(), session=session)
    assert fetched.deletable is False

    async with session_factory() as session:
        listed = await docs.list_documents(principal=_principal(), session=session)
    assert next(d for d in listed if d.uid == doc.uid).deletable is False


async def test_a_document_written_before_the_column_existed_is_deletable(
    session_factory,
    firm,
    super_engine,
    monkeypatch,
) -> None:
    """The DEFAULT is what protects every existing row: 0122 adds the column to a table already
    full of documents, and a NULL or False default would have made the whole back catalogue
    undeletable overnight."""
    doc = await _upload(session_factory, monkeypatch, deletable=True)
    # Simulate a pre-0122 row by writing the column the way the old INSERT did: not at all.
    with super_engine.connect() as c:
        stored = c.execute(
            text("SELECT deletable FROM documents.documents WHERE uid = :u"), {"u": doc.uid}
        ).scalar_one()
    assert stored is True
