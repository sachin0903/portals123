"""Endpoint test for the client form-render path: GET request-item form (elevated, owner-checked).

A client (empty tenant lineage) fetches the builder-form definition for a `complete_form` item of
their OWN request via the elevated service_role endpoint. Proves the owner gets the form + fields
and a non-owner is refused — the ownership check is the whole authorisation boundary.
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
from app.db import FirmContext
from app.routers import documents as documents_router


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """A tenant + published form (one field) + a client's complete_form request pointing at it."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"rten_{s}",
                    "alias": f"rfirm-{s}",
                    "name": f"RFirm {s}",
                },
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
                        "uid": f"rusr_{tag}_{s}",
                        "email": f"{tag}_{s}@r.test",
                        "wos": f"rwos_{tag}_{s}",
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
        # The client is the CRM contact; the client login maps to it via crm.contact_users.
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"rcnt_{s}", "name": "Client", "email": f"client_{s}@r.test"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) "
                "VALUES (:t, :cid, CAST(:u AS uuid))"
            ),
            {"t": tenant, "cid": contact, "u": client},
        )
        form_uid = f"frm_{s}"
        form_id = c.execute(
            text(
                "INSERT INTO documents.forms (uid, tenant_id, code, name, status, created_by) "
                "VALUES (:uid, :t, :code, 'Fact Find', 'published', CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": form_uid, "t": tenant, "code": f"ff_{s}", "by": staff},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.form_fields "
                "  (uid, tenant_id, form_id, field_key, label, required, sort_order) "
                "VALUES (:uid, :t, :fid, 'full_name', 'Full name', true, 1)"
            ),
            {"uid": f"fld_{s}", "t": tenant, "fid": form_id},
        )
        req_id = c.execute(
            text(
                "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                "VALUES (:uid, :t, :cid, CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": f"req_{s}", "t": tenant, "cid": contact, "by": staff},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, form_uid) "
                "VALUES (:uid, :rid, :t, 'complete_form', 'Fill it', :form)"
            ),
            {"uid": f"rqi_{s}", "rid": req_id, "t": tenant, "form": form_uid},
        )
    return {
        "tenant": tenant,
        "client": client,
        "contact": contact,
        "staff": staff,
        "org": f"rorg_{s}",
        "req": f"req_{s}",
        "item": f"rqi_{s}",
        "form_uid": form_uid,
    }


@pytest_asyncio.fixture
async def firm_ctx(db: dict[str, str]):
    """Build a FirmContext (service_role + published claims) for a given acting user — mirrors
    db.py::firm_session without the WorkOS resolve step."""
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
            # contact_scope() reads this; the owner's scope includes their contact, a non-owner's
            # does not — that mismatch is what the ownership check turns into a 404.
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
async def test_owner_gets_form_and_fields(firm_ctx, seed: dict[str, object]) -> None:
    async with firm_ctx(
        seed["client"], seed["tenant"], seed["org"], contact_ids=[seed["contact"]]
    ) as ctx:
        out = await documents_router.get_request_form(
            req_uid=seed["req"], item_uid=seed["item"], ctx=ctx
        )
    assert out.form_uid == seed["form_uid"]
    assert out.name == "Fact Find"
    assert [f.field_key for f in out.fields] == ["full_name"]
    assert out.fields[0].required is True


@pytest.mark.asyncio
async def test_non_owner_refused(firm_ctx, seed: dict[str, object]) -> None:
    # The staff user isn't the request owner: their contact_scope is empty, so the request's
    # contact_id isn't in it and the item doesn't resolve.
    async with firm_ctx(seed["staff"], seed["tenant"], seed["org"]) as ctx:
        with pytest.raises(HTTPException) as err:
            await documents_router.get_request_form(
                req_uid=seed["req"], item_uid=seed["item"], ctx=ctx
            )
    assert err.value.status_code == 404
