"""A published template version snapshots its linked input form.

Editing the form afterwards must NOT change an already-published template — generation reads the
frozen snapshot. Republishing re-captures the current form. Drives the /templates router coroutines
against a real authenticated session; the form + fields are seeded directly.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import templates as tpl

# A version blob that passes the publish content-check (legacy TemplateContent shape).
PAGE = {"pages": [{"uid": "p1", "blocks": [{"uid": "b1", "type": "heading", "text": "Hi"}]}]}


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"sten_{s}", "alias": f"sfirm-{s}", "name": "SFirm"},
            ).scalar_one()
        )
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Sn', 'Staff', true) RETURNING id"
                ),
                {"uid": f"susr_{s}", "email": f"staff_{s}@s.test", "wos": f"swos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )
    return {"tenant_id": str(tenant), "user_uuid": user, "org": f"sorg_{s}", "wos": f"swos_{s}"}


def _principal(staff: dict[str, str]) -> Principal:
    return Principal(
        user_id=staff["wos"],
        organisation_id=staff["org"],
        role="adviser",
        permissions=["templates:write"],
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": staff["wos"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["templates:write"],
        "org_id": staff["org"],
        "user_uuid": staff["user_uuid"],
        "tenant_id": int(staff["tenant_id"]),
        "tenant_ids": [int(staff["tenant_id"])],
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


def _seed_form(super_engine: Engine, tenant_id: int, code: str, field_keys: list[str]) -> None:
    """Create a published form with the given field keys (in order)."""
    with super_engine.connect() as c:
        form_id = int(
            c.execute(
                text(
                    "INSERT INTO documents.forms (uid, tenant_id, code, name, status) "
                    "VALUES (:uid, :t, :code, :name, 'published') RETURNING id"
                ),
                {"uid": f"frm_{uuid.uuid4().hex}", "t": tenant_id, "code": code, "name": code},
            ).scalar_one()
        )
        for i, key in enumerate(field_keys):
            c.execute(
                text(
                    "INSERT INTO documents.form_fields "
                    "  (uid, tenant_id, form_id, field_key, field_type, label, sort_order) "
                    "VALUES (:uid, :t, :fid, :key, 'text', :label, :ord)"
                ),
                {
                    "uid": f"fld_{uuid.uuid4().hex}",
                    "t": tenant_id,
                    "fid": form_id,
                    "key": key,
                    "label": key.title(),
                    "ord": i,
                },
            )
        c.commit()


def _add_field(super_engine: Engine, tenant_id: int, code: str, field_key: str) -> None:
    with super_engine.connect() as c:
        form_id = int(
            c.execute(
                text("SELECT id FROM documents.forms WHERE code = :code AND tenant_id = :t"),
                {"code": code, "t": tenant_id},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO documents.form_fields "
                "  (uid, tenant_id, form_id, field_key, field_type, label, sort_order) "
                "VALUES (:uid, :t, :fid, :key, 'text', :label, 99)"
            ),
            {
                "uid": f"fld_{uuid.uuid4().hex}",
                "t": tenant_id,
                "fid": form_id,
                "key": field_key,
                "label": field_key.title(),
            },
        )
        c.commit()


async def _publish_v(session_factory, principal, uid: str) -> None:
    """Save content into the latest draft and publish it."""
    async with session_factory() as session:
        v = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
    async with session_factory() as session:
        await tpl.publish_template_version(uid=uid, vid=v.uid, principal=principal, session=session)


@pytest.mark.asyncio
async def test_publish_snapshots_form_and_is_immune_to_edits(
    session_factory, staff, super_engine
) -> None:
    principal = _principal(staff)
    tenant = int(staff["tenant_id"])
    code = f"ff_{uuid.uuid4().hex[:6]}"
    _seed_form(super_engine, tenant, code, ["first_name", "surname"])

    # Template linked to the form.
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"tpl_{uuid.uuid4().hex[:6]}", name="Illus", document_type="illustration"
            ),
            principal=principal,
            session=session,
        )
        uid = created.uid
    async with session_factory() as session:
        await tpl.update_template(
            uid=uid,
            body=tpl.UpdateTemplate(input_form_code=code),
            principal=principal,
            session=session,
        )

    # Publish → the version freezes the form's two fields.
    await _publish_v(session_factory, principal, uid)
    async with session_factory() as session:
        form = await tpl.template_input_form(uid=uid, principal=principal, session=session)
    assert form.source == "snapshot"
    assert [f.field_key for f in form.fields] == ["first_name", "surname"]

    # Edit the form AFTER publish — the published template must not change.
    _add_field(super_engine, tenant, code, "dob")
    async with session_factory() as session:
        still = await tpl.template_input_form(uid=uid, principal=principal, session=session)
    assert still.source == "snapshot"
    assert [f.field_key for f in still.fields] == ["first_name", "surname"]  # immune

    # Republish → re-captures the now-3-field form.
    await _publish_v(session_factory, principal, uid)
    async with session_factory() as session:
        after = await tpl.template_input_form(uid=uid, principal=principal, session=session)
    assert after.source == "snapshot"
    assert [f.field_key for f in after.fields] == ["first_name", "surname", "dob"]


@pytest.mark.asyncio
async def test_input_form_none_without_linked_form(session_factory, staff) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"tpl_{uuid.uuid4().hex[:6]}", name="No form", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        uid = created.uid
    await _publish_v(session_factory, principal, uid)
    async with session_factory() as session:
        form = await tpl.template_input_form(uid=uid, principal=principal, session=session)
    assert form.source == "none"
    assert form.fields == []
