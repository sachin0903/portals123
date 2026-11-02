"""Sharing one illustration puts it in the client portal.

The firm flag files every new illustration on render. This is the other half: a person
chooses one row, and the subject link — the client's read permission — is bound for that
PDF only. A second share does not add a second link.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import illustrations


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, object]:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"gten_{s}", "alias": f"sfirm-{s}", "name": f"SFirm {s}"},
            ).scalar_one()
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', 'Ada Client', :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"c{s}abc", "email": f"ada_{s}@s.test"},
            ).scalar_one()
        )
        doc_id = str(
            c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, name, status, intended_contact_id) "
                    "VALUES (:uid, :t, 'illustration', 'generated', 'SIPP illustration', "
                    "        'generated', :cid) "
                    "RETURNING id"
                ),
                {"uid": f"d{s}abc", "t": tenant, "cid": contact},
            ).scalar_one()
        )
        illustration = str(
            c.execute(
                text(
                    "INSERT INTO platform.illustrations "
                    "  (uid, tenant_id, contact_id, document_id, name, product_type, inputs) "
                    "VALUES (:uid, :t, :cid, :did, 'SIPP illustration', 'SIPP', '{}'::jsonb) "
                    "RETURNING uid"
                ),
                {"uid": f"i{s}abc", "t": tenant, "cid": contact, "did": doc_id},
            ).scalar_one()
        )
        queued = str(
            c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, name, status, intended_contact_id) "
                    "VALUES (:uid, :t, 'illustration', 'generated', 'Still rendering', "
                    "        'queued', :cid) "
                    "RETURNING id"
                ),
                {"uid": f"q{s}abc", "t": tenant, "cid": contact},
            ).scalar_one()
        )
        queued_illustration = str(
            c.execute(
                text(
                    "INSERT INTO platform.illustrations "
                    "  (uid, tenant_id, contact_id, document_id, name, product_type, inputs) "
                    "VALUES (:uid, :t, :cid, :did, 'Still rendering', 'SIPP', '{}'::jsonb) "
                    "RETURNING uid"
                ),
                {"uid": f"j{s}abc", "t": tenant, "cid": contact, "did": queued},
            ).scalar_one()
        )
        c.commit()
    return {
        "tenant_id": tenant,
        "contact_id": contact,
        "illustration": illustration,
        "queued": queued_illustration,
    }


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], firm: dict[str, object]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": "share-test",
        "role": "adviser",
        "portal": "platform",
        "permissions": ["documents:send", "contacts:read_all"],
        "user_uuid": None,
        "tenant_id": int(firm["tenant_id"]),  # type: ignore[arg-type]
        "tenant_ids": [int(firm["tenant_id"])],  # type: ignore[arg-type]
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
async def test_sharing_one_illustration_files_it_under_the_client(session_factory, firm) -> None:
    principal = _principal(["documents:send"])
    contact_id = int(firm["contact_id"])
    async with session_factory() as session:
        before = await illustrations.list_illustrations(contact_id=contact_id, session=session)
        held = next(row for row in before if row.uid == firm["illustration"])
        assert held.shared_with_client is False

        shared = await illustrations.share_illustration(
            illustration_uid=str(firm["illustration"]),
            contact_id=contact_id,
            principal=principal,
            session=session,
        )
        assert shared.shared_with_client is True
        # A second share is the same link, not a second one.
        await illustrations.share_illustration(
            illustration_uid=str(firm["illustration"]),
            contact_id=contact_id,
            principal=principal,
            session=session,
        )
        links = (
            (
                await session.execute(
                    text(
                        "SELECT dc.role FROM documents.document_contacts dc "
                        "JOIN platform.illustrations i ON i.document_id = dc.document_id "
                        "WHERE i.uid = :uid AND dc.contact_id = :cid"
                    ),
                    {"uid": firm["illustration"], "cid": contact_id},
                )
            )
            .scalars()
            .all()
        )
        after = await illustrations.list_illustrations(contact_id=contact_id, session=session)
        shown = next(row for row in after if row.uid == firm["illustration"])

    assert links == ["subject"]
    assert shown.shared_with_client is True


@pytest.mark.asyncio
async def test_an_illustration_that_has_not_rendered_cannot_be_shared(
    session_factory, firm
) -> None:
    with pytest.raises(HTTPException) as caught:
        async with session_factory() as session:
            await illustrations.share_illustration(
                illustration_uid=str(firm["queued"]),
                contact_id=int(firm["contact_id"]),
                principal=_principal(["documents:send"]),
                session=session,
            )
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_sharing_takes_documents_send(session_factory, firm) -> None:
    with pytest.raises(HTTPException) as caught:
        async with session_factory() as session:
            await illustrations.share_illustration(
                illustration_uid=str(firm["illustration"]),
                contact_id=int(firm["contact_id"]),
                principal=_principal(["illustrations:write"]),
                session=session,
            )
    assert caught.value.status_code == 403
