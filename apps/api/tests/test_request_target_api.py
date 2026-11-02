"""The document a client must READ before they agree: metadata, then bytes.

Both routes are elevated (``portal_session``) and their ownership predicate over
``auth.contact_scope()`` is the whole authorisation boundary — a client legitimately reviews a
firm's own terms PDF, which they hold no ``document_contacts`` link to, so the ordinary
``/documents/{uid}/content`` is closed to them.

What this pins beyond that: ``/target`` answers with a name and a content type and NO URL. It used
to answer with a presigned object-store URL, which the portal cannot render — the store has no
public endpoint in a container deployment, and the portal draws a PDF with pdf.js, which fetches
the bytes rather than navigating to them, so a cross-origin URL needs CORS the store does not
send. Both failures show a client the same thing: a frame that never fills.
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

from app import storage
from app.auth import Principal
from app.db import FirmContext
from app.routers import documents as documents_router

PDF = b"%PDF-1.4 declaration"


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """A tenant, a client login mapped to its contact, and that client's `acknowledge` item
    pointing at an uploaded PDF (the shape a firm's own client agreement takes)."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"tten_{s}", "alias": f"tfirm-{s}", "name": f"TFirm {s}"},
            ).scalar_one()
        )

        def mk_user(tag: str, portal: str) -> str:
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                        "VALUES (:uid, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"tusr_{tag}_{s}",
                        "email": f"{tag}_{s}@t.test",
                        "wos": f"twos_{tag}_{s}",
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": uid, "t": tenant, "p": portal},
            )
            return uid

        staff = mk_user("staff", "platform")
        client = mk_user("client", "client")
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"tcnt_{s}", "name": "Client", "email": f"client_{s}@t.test"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) "
                "VALUES (:t, :cid, CAST(:u AS uuid))"
            ),
            {"t": tenant, "cid": contact, "u": client},
        )
        key = f"files/documents/tdoc_{s}/original.pdf"
        file_id = c.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (:t, :b, :k, 'application/pdf', :n, 'sha') RETURNING id"
            ),
            {"t": tenant, "b": f"t-tten_{s}", "k": key, "n": len(PDF)},
        ).scalar_one()
        doc_id = c.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, original_file_id) "
                "VALUES (:uid, :t, 'terms', 'uploaded', :name, 'generated', :f) RETURNING id"
            ),
            {
                "uid": f"tdoc_{s}",
                "t": tenant,
                "name": "Client Agreement v4.pdf",
                "f": file_id,
            },
        ).scalar_one()
        req_id = c.execute(
            text(
                "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                "VALUES (:uid, :t, :cid, CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": f"treq_{s}", "t": tenant, "cid": contact, "by": staff},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, target_document_id) "
                "VALUES (:uid, :rid, :t, 'acknowledge', 'Accept the terms', :doc)"
            ),
            {"uid": f"trqi_{s}", "rid": req_id, "t": tenant, "doc": doc_id},
        )
    return {
        "tenant": tenant,
        "client": client,
        "staff": staff,
        "contact": contact,
        "org": f"torg_{s}",
        "req": f"treq_{s}",
        "item": f"trqi_{s}",
        "bucket": f"t-tten_{s}",
        "key": key,
    }


@pytest_asyncio.fixture
async def firm_ctx(db: dict[str, str]):
    """A FirmContext (service_role + published claims) for a given acting user — db.py's
    ``portal_session`` without the WorkOS resolve step."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _open(
        user_uuid: str, tenant: int, org: str, contact_ids: list[int] | None = None
    ) -> AsyncIterator[FirmContext]:
        claims = {
            "sub": "wos",
            "role": "client",
            "portal": "client",
            "permissions": [],
            "org_id": org,
            "user_uuid": user_uuid,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            # contact_scope() reads this: the owner's scope holds their contact, a non-owner's does
            # not, and that mismatch is what the ownership predicate turns into a 404.
            "contact_ids": contact_ids or [],
            "account_ids": [],
        }
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            yield FirmContext(
                session=session,
                tenant_id=tenant,
                principal=Principal(user_id="wos", organisation_id=org),
            )

    yield _open
    await engine.dispose()


@pytest.mark.asyncio
async def test_owner_gets_name_and_type_but_no_url(firm_ctx, seed: dict[str, object]) -> None:
    async with firm_ctx(
        seed["client"], seed["tenant"], seed["org"], contact_ids=[seed["contact"]]
    ) as ctx:
        out = await documents_router.get_request_target(
            req_uid=seed["req"], item_uid=seed["item"], ctx=ctx
        )
    assert out == {"name": "Client Agreement v4.pdf", "content_type": "application/pdf"}


@pytest.mark.asyncio
async def test_owner_gets_the_bytes_inline(
    firm_ctx, seed: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: dict[str, object] = {}

    async def fake_get(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        asked["key"], asked["bucket"] = key, bucket
        return PDF, "application/pdf"

    monkeypatch.setattr(storage, "get_document", fake_get)
    async with firm_ctx(
        seed["client"], seed["tenant"], seed["org"], contact_ids=[seed["contact"]]
    ) as ctx:
        res = await documents_router.get_request_target_content(
            req_uid=seed["req"], item_uid=seed["item"], ctx=ctx
        )
    assert res.body == PDF
    assert res.media_type == "application/pdf"
    # Inline, or the portal's proxy hands the browser a download instead of a preview.
    assert res.headers["content-disposition"].startswith("inline")
    # The tenant's OWN bucket, read off the file row — the key carries no tenant segment.
    assert asked == {"key": seed["key"], "bucket": seed["bucket"]}


@pytest.mark.asyncio
async def test_non_owner_refused_on_both_routes(firm_ctx, seed: dict[str, object]) -> None:
    async with firm_ctx(seed["staff"], seed["tenant"], seed["org"]) as ctx:
        for call in (
            documents_router.get_request_target,
            documents_router.get_request_target_content,
        ):
            with pytest.raises(HTTPException) as err:
                await call(req_uid=seed["req"], item_uid=seed["item"], ctx=ctx)
            assert err.value.status_code == 404
