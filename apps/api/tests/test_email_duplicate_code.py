"""`DuplicateCode` means the code is taken — and nothing else.

`create_template`/`update_template` translate an `IntegrityError` into `DuplicateCode`, which
`routers/email_templates.py` answers as 409 "An email template with this code already exists.".
`email.templates` carries other constraints a write can breach, so a blanket translation reports a
taken code for a breach that has nothing to do with the code: an operator naming a
tenant that does not exist (`templates_tenant_id_fkey`) was told to pick another code, and a
`new_id()` collision (`templates_uid_key`) would say the same. Only `templates_tenant_id_code_key`
is that message.

Pinned here: the real duplicate on both write paths; the two impostor constraints re-raised rather
than relabelled; and the tenant one answered at its source — `_tenant_scope_permission` refuses an
unknown tenant with 404 for every `/admin/tenants/{tenant_id}/...` endpoint.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import templates_service as svc
from app.routers import admin
from tests import email_support

# Higher than any serial this suite mints, so it names no tenant at all.
UNKNOWN_TENANT = 10**9


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, prefix="d", name="DFirm", with_operator=True)


@pytest_asyncio.fixture
async def firm_session(db: dict[str, str], firm: dict[str, str]):
    """The firm router's own path: `authenticated` + claims, so the RLS policies are exercised."""
    opener, engine = email_support.session_opener(db, email_support.firm_claims(firm))
    yield opener
    await engine.dispose()


@pytest_asyncio.fixture
async def ops_session(db: dict[str, str], firm: dict[str, str]):
    """The admin door: `service_role` + the operator's claims (mirrors db.admin_session), so it can
    be driven at a tenant the firm session's RLS would refuse before Postgres saw the FK."""
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims(firm["operator_uuid"]), role="service_role"
    )
    yield opener
    await engine.dispose()


def _code(label: str) -> str:
    return f"{label}_{uuid.uuid4().hex[:6]}"


async def _create(session: AsyncSession, tenant_id: int, actor: str, code: str) -> dict:
    return await svc.create_template(
        session,
        tenant_id,
        actor,
        code=code,
        name="Dupe",
        category="notification",
        description=None,
        scope="firm",
    )


@pytest.mark.asyncio
async def test_taken_code_is_a_duplicate_on_both_write_paths(
    firm_session, firm: dict[str, str]
) -> None:
    tenant_id = int(firm["tenant_id"])
    actor = firm["user_uuid"]
    taken = _code("taken")

    async with firm_session() as session:
        await _create(session, tenant_id, actor, taken)
        other = await _create(session, tenant_id, actor, _code("other"))

    async with firm_session() as session:
        with pytest.raises(svc.DuplicateCode):
            await _create(session, tenant_id, actor, taken)

    async with firm_session() as session:
        with pytest.raises(svc.DuplicateCode):
            await svc.update_template(
                session,
                tenant_id,
                other["uid"],
                name=None,
                code=taken,
                category=None,
                description=None,
            )


@pytest.mark.asyncio
async def test_unknown_tenant_is_not_reported_as_a_taken_code(
    ops_session, firm: dict[str, str]
) -> None:
    """The reachable case: an operator mistypes the tenant id in the URL. The FK is not a verdict
    on the code, so it surfaces as itself."""
    async with ops_session() as session:
        with pytest.raises(IntegrityError) as caught:
            await _create(session, UNKNOWN_TENANT, firm["operator_uuid"], _code("orphan"))
    assert caught.value.orig.diag.constraint_name == "templates_tenant_id_fkey"


@pytest.mark.asyncio
async def test_uid_collision_is_not_reported_as_a_taken_code(
    firm_session, firm: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nanoid collision IS a unique violation, so the sqlstate alone would still say "that code
    is taken". The constraint name is what tells the two apart."""
    tenant_id = int(firm["tenant_id"])
    actor = firm["user_uuid"]

    async with firm_session() as session:
        first = await _create(session, tenant_id, actor, _code("uidfirst"))

    monkeypatch.setattr(svc, "_new_template_uid", lambda: str(first["uid"]))
    async with firm_session() as session:
        with pytest.raises(IntegrityError) as caught:
            await _create(session, tenant_id, actor, _code("uidclash"))
    assert caught.value.orig.diag.constraint_name == "templates_uid_key"


@pytest.mark.asyncio
async def test_unknown_tenant_scope_is_not_found(service_session: AsyncSession) -> None:
    """`_tenant_scope_permission` is where an unknown tenant id has to stop: it read `is_model` as
    false and handed back `tenants:write`, so the request went on to fail as whatever the handler
    happened to do next."""
    with pytest.raises(HTTPException) as caught:
        await admin._tenant_scope_permission(service_session, UNKNOWN_TENANT, write=True)
    assert caught.value.status_code == 404

    with pytest.raises(HTTPException) as caught:
        await admin._tenant_scope_permission(service_session, UNKNOWN_TENANT, write=False)
    assert caught.value.status_code == 404
