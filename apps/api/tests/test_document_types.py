"""Behaviour tests for the managed document-type taxonomy (documents.document_types).

The seed trigger gives every tenant the default vocabulary on creation (0040 seeds BOTH document
and supporting-file types, split by ``kind``); a role with ``document_types:write`` edits it; the
tags are open-ended, so upload + reclassify NORMALISE and REGISTER any new document_type rather than
validating against a closed set. Runs against a real Postgres; object storage is mocked.
"""

import io
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers, UploadFile

from app import storage
from app.auth import Principal
from app.routers import documents as docs

PDF = b"%PDF-1.4 minimal\n"
DEFAULT_SLUGS = {
    "report",
    "statement",
    "agreement",
    "identity",
    "tax",
    "illustration",
    "application",
    "letter",
    "fatca_form",
}
# 0040 added supporting-FILE types to the same registry (kind='file'), seeded alongside the docs.
FILE_SLUGS = {"passport", "national_id", "proof_of_address", "other"}


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """A tenant (which the 0037 trigger seeds with the default types) + a staff user."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"tt_{s}", "a": f"tt-{s}", "n": "TT"},
            ).scalar_one()
        )
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                    "VALUES (:u, :e, :w, true) RETURNING id"
                ),
                {"u": f"tu_{s}", "e": f"s_{s}@t.test", "w": f"tw_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": staff, "t": tenant},
        )
        c.commit()
    return {"tenant": tenant, "staff": staff, "org": f"tto_{s}"}


def _principal(perms: list[str]) -> Principal:
    # role='adviser' so is_system() is false and require_permission actually enforces `perms`.
    return Principal(user_id="wos", organisation_id="org", role="adviser", permissions=perms)


def _upload(name: str = "d.pdf") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(PDF),
        size=len(PDF),
        filename=name,
        headers=Headers({"content-type": "application/pdf"}),
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], seed: dict[str, object]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": "wos",
        "role": "adviser",
        "portal": "platform",
        "permissions": [],
        "org_id": seed["org"],
        "user_uuid": seed["staff"],
        "tenant_id": int(seed["tenant"]),
        "tenant_ids": [int(seed["tenant"])],
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


@pytest.mark.asyncio
async def test_defaults_seeded_on_tenant_creation(session_factory) -> None:
    async with session_factory() as session:
        doc_types = await docs.list_document_types(
            kind="document", principal=_principal([]), session=session
        )
        file_types = await docs.list_document_types(
            kind="file", principal=_principal([]), session=session
        )
    assert {t.slug for t in doc_types} == DEFAULT_SLUGS
    assert {t.slug for t in file_types} == FILE_SLUGS  # 0040 seeds supporting-file types too
    assert all(not t.archived for t in doc_types)
    # Ordered by sort_order: report first (1), the W-9 / W-8 last (10, 0261).
    assert doc_types[0].slug == "report" and doc_types[-1].slug == "fatca_form"
    assert doc_types[-1].label == "W-9 / W-8"


@pytest.mark.asyncio
async def test_create_requires_admin(session_factory) -> None:
    body = docs.DocumentTypeIn(slug="suitability", label="Suitability report")
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:  # documents:write is NOT enough
            await docs.create_document_type(
                body=body, principal=_principal(["documents:write"]), session=session
            )
        assert ei.value.status_code == 403
        out = await docs.create_document_type(
            body=body, principal=_principal(["document_types:write"]), session=session
        )
    assert out.slug == "suitability"
    assert out.label == "Suitability report"


@pytest.mark.asyncio
async def test_create_rejects_bad_slug_and_duplicate(session_factory) -> None:
    admin = _principal(["document_types:write"])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as bad:
            await docs.create_document_type(
                body=docs.DocumentTypeIn(slug="Not A Slug!", label="x"),
                principal=admin,
                session=session,
            )
        assert bad.value.status_code == 422
        with pytest.raises(HTTPException) as dup:  # 'report' is seeded
            await docs.create_document_type(
                body=docs.DocumentTypeIn(slug="report", label="Report again"),
                principal=admin,
                session=session,
            )
        assert dup.value.status_code == 409


@pytest.mark.asyncio
async def test_update_archive_hides_from_active_list(session_factory) -> None:
    admin = _principal(["document_types:write"])
    async with session_factory() as session:
        created = await docs.create_document_type(
            body=docs.DocumentTypeIn(slug="temp", label="Temp"), principal=admin, session=session
        )
        updated = await docs.update_document_type(
            type_id=created.id,
            body=docs.DocumentTypeUpdate(label="Renamed", archived=True),
            principal=admin,
            session=session,
        )
        assert updated.label == "Renamed" and updated.archived is True

        active = await docs.list_document_types(principal=_principal([]), session=session)
        assert "temp" not in {t.slug for t in active}
        witharchived = await docs.list_document_types(
            include_archived=True, principal=_principal([]), session=session
        )
        assert "temp" in {t.slug for t in witharchived}

        with pytest.raises(HTTPException) as ei:
            await docs.update_document_type(
                type_id=999999,
                body=docs.DocumentTypeUpdate(label="x"),
                principal=admin,
                session=session,
            )
        assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_upload_registers_new_open_ended_tag(session_factory, monkeypatch) -> None:
    """Types are open-ended: a seeded tag classifies fine, and a brand-new custom tag is accepted,
    normalised to a slug, and registered as a reusable suggestion (no 422). A blank type and the
    reserved 'auto' sentinel are both rejected (the type is required; 'auto' must be resolved to a
    concrete tag client-side before upload)."""

    async def _put(key, data, content_type, **_kw):
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    writer = _principal(["documents:write"])
    async with session_factory() as session:
        ok = await docs.upload_document(
            file=[_upload()],
            document_type="report",
            name=None,
            subject_contact_id=None,
            principal=writer,
            session=session,
        )
        assert ok.documents[0].document_type == "report"
        # A new custom tag is accepted, slugified, and registered with the typed label.
        made = await docs.upload_document(
            file=[_upload()],
            document_type="Suitability Report",
            name=None,
            subject_contact_id=None,
            principal=writer,
            session=session,
        )
        assert made.documents[0].document_type == "suitability_report"
        listed = await docs.list_document_types(principal=writer, session=session)
        tags = {t.slug: t.label for t in listed}
        assert tags.get("suitability_report") == "Suitability Report"
        # Required + reserved: blank/whitespace, the 'auto' sentinel, and 'other' are all rejected.
        for bad in ("", "   ", "auto", "other", "Other"):
            with pytest.raises(HTTPException) as ei:
                await docs.upload_document(
                    file=[_upload()],
                    document_type=bad,
                    name=None,
                    subject_contact_id=None,
                    principal=writer,
                    session=session,
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_reclassify_registers_new_open_ended_tag(session_factory, monkeypatch) -> None:
    """Reclassify is open-ended too: a new tag is accepted + registered, not rejected."""

    async def _put(key, data, content_type, **_kw):
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    writer = _principal(["documents:write"])
    async with session_factory() as session:
        res = await docs.upload_document(
            file=[_upload()],
            document_type="report",
            name=None,
            subject_contact_id=None,
            principal=writer,
            session=session,
        )
        uid = res.documents[0].uid
        good = await docs.update_document(
            uid=uid,
            body=docs.UpdateDocument(document_type="statement"),
            principal=writer,
            session=session,
        )
        assert good.document_type == "statement"
        made = await docs.update_document(
            uid=uid,
            body=docs.UpdateDocument(document_type="Meeting Notes"),
            principal=writer,
            session=session,
        )
        assert made.document_type == "meeting_notes"
        tags = {t.slug for t in await docs.list_document_types(principal=writer, session=session)}
        assert "meeting_notes" in tags


@pytest.mark.asyncio
async def test_classify_type_suggests_from_tags(session_factory) -> None:
    """The classify endpoint suggests a tag from the tenant's active tags by filename (no save);
    it never returns the 'auto' sentinel."""
    writer = _principal(["documents:write"])
    async with session_factory() as session:
        hit = await docs.classify_document_type(
            body=docs.ClassifyRequest(filename="client_passport_scan.pdf"),
            principal=writer,
            session=session,
        )
        assert hit.document_type == "identity" and hit.label  # passport → identity (seeded hint)
        # No confident match ⇒ a BLANK suggestion (never 'other'/a default) — the adviser must pick.
        miss = await docs.classify_document_type(
            body=docs.ClassifyRequest(filename="zzzz.bin"), principal=writer, session=session
        )
        assert miss.document_type == "" and miss.label == ""


def test_existing_tenants_backfilled_during_migration(super_engine: Engine) -> None:
    """Regression (review HIGH): 0037's existing-tenant backfill runs during ``alembic upgrade`` as
    the ``migrator`` role, which is subject to FORCE RLS on auth.tenants and does NOT carry
    BYPASSRLS (it isn't inherited via service_role membership) — so a bare ``SELECT FROM
    auth.tenants`` sees zero rows and the seed silently inserts nothing, leaving every pre-existing
    tenant with an empty taxonomy (every upload then 422s). The seed must ``SET LOCAL ROLE
    service_role`` around the INSERT. The model tenant is the canary: it's seeded in 0001, so it
    predates 0037 and only gets its defaults via the backfill (not the AFTER-INSERT trigger), and
    it must carry the full set."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT dt.slug FROM documents.document_types dt "
                    "JOIN auth.tenants t ON t.id = dt.tenant_id "
                    "WHERE t.is_model AND dt.kind = 'document'"
                )
            )
            .scalars()
            .all()
        )
    assert set(rows) == DEFAULT_SLUGS, (
        "0037 backfill seeded no default types for the pre-existing model tenant — the seed likely "
        "ran under migrator without SET LOCAL ROLE service_role (FORCE RLS returned zero tenants)"
    )


@pytest.mark.asyncio
async def test_update_rejects_blank_label(session_factory) -> None:
    """Review LOW: a blank label would violate the table CHECK; the update path must reject it
    cleanly (422) rather than let the constraint surface as an unhandled 500."""
    admin = _principal(["document_types:write"])
    async with session_factory() as session:
        created = await docs.create_document_type(
            body=docs.DocumentTypeIn(slug="temp_blank", label="Temp"),
            principal=admin,
            session=session,
        )
        for blank in ("", "   "):
            with pytest.raises(HTTPException) as ei:
                await docs.update_document_type(
                    type_id=created.id,
                    body=docs.DocumentTypeUpdate(label=blank),
                    principal=admin,
                    session=session,
                )
            assert ei.value.status_code == 422
        # A None label (archive-only update) is still allowed — it leaves the label untouched.
        untouched = await docs.update_document_type(
            type_id=created.id,
            body=docs.DocumentTypeUpdate(archived=True),
            principal=admin,
            session=session,
        )
        assert untouched.label == "Temp" and untouched.archived is True
