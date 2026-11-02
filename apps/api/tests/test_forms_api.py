"""Endpoint-behaviour tests for the /forms router.

Complements the RLS proof (test_forms_rls.py) by driving the router coroutines directly against a
real ``authenticated`` session (role + claims set exactly like db.py::tenant_session), so the SQL,
the jsonb round-trips and the pydantic shaping are all exercised — not just the RLS predicates.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import forms as forms_router
from tests.conftest import assert_public_id


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    """A tenant + one platform staff user, seeded past RLS. Returns the claim inputs."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"aten_{s}",
                    "alias": f"afirm-{s}",
                    "name": f"AFirm {s}",
                },
            ).scalar_one()
        )
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Ada', 'Staff', true) RETURNING id"
                ),
                {
                    "uid": f"ausr_{s}",
                    "email": f"staff_{s}@afirm.test",
                    "wos": f"awos_{s}",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )
        # A crm.contact for the tenant — requests/form_responses now bind to a contact_id.
        contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :uid, 'individual', 'Test Client') RETURNING id"
                ),
                {"t": tenant, "uid": f"acon_{s}"},
            ).scalar_one()
        )
    return {
        "tenant_id": str(tenant),
        "user_uuid": user,
        "org": f"aorg_{s}",
        "wos": f"awos_{s}",
        "contact_id": str(contact_id),
    }


def _principal(staff: dict[str, str], perms: list[str]) -> Principal:
    return Principal(
        user_id=staff["wos"], organisation_id=staff["org"], role="adviser", permissions=perms
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    """An async context manager yielding a session acting as `authenticated` with the staff claims
    published — one short transaction per `async with`, mirroring the request path."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": staff["wos"],
        "role": "adviser",
        "portal": "platform",
        # contacts:read_all → a full-book adviser: since 0022 requests/request_items are
        # book-scoped (contact_scope), so a book-scoped session with empty contact_ids couldn't
        # read back a request it just inserted (INSERT ... RETURNING needs the row visible). These
        # tests exercise the forms/requests flow, not book scoping, so model a full-book adviser.
        "permissions": ["forms:write", "contacts:read_all"],
        "org_id": staff["org"],
        "user_uuid": staff["user_uuid"],
        "tenant_id": int(staff["tenant_id"]),
        "tenant_ids": [int(staff["tenant_id"])],
        "contact_ids": [],
        "account_ids": [],
    }

    @asynccontextmanager
    async def _open() -> AsyncIterator:
        import json

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
async def test_form_lifecycle(session_factory, staff: dict[str, str]) -> None:
    # forms:write authors the form; forms:submit (split out in 0042, backfilled to write
    # holders) covers the standalone response it records and lists at the end.
    principal = _principal(staff, ["forms:write", "forms:submit"])
    code = f"fact_find_{uuid.uuid4().hex[:6]}"

    # create → draft, no fields, creator name projected.
    async with session_factory() as session:
        created = await forms_router.create_form(
            body=forms_router.CreateForm(code=code, name="Fact Find", entity_type="applicant"),
            principal=principal,
            session=session,
        )
    assert created.status == "draft"
    assert created.field_count == 0
    assert created.created_by == "Ada Staff"
    uid = created.uid

    # list + get see it.
    async with session_factory() as session:
        listed = await forms_router.list_forms(principal=principal, session=session)
        got = await forms_router.get_form(uid=uid, principal=principal, session=session)
    assert uid in {f.uid for f in listed}
    assert got.code == code

    # set fields: a placeholder-uid field + a conditional/options field. sort_order is renumbered,
    # placeholder uid is replaced with a persisted 'fld_' one, jsonb round-trips.
    async with session_factory() as session:
        fields = await forms_router.set_form_fields(
            uid=uid,
            body=forms_router.SetFields(
                fields=[
                    forms_router.FormFieldIn(uid="fld-new-1", field_key="name", label="Name"),
                    forms_router.FormFieldIn(
                        field_key="crypto_years",
                        field_type="number",
                        label="Years",
                        required=True,
                        conditions={
                            "conditional": {
                                "field": "products",
                                "operator": "contains",
                                "value": "crypto",
                            }
                        },
                        options={"currency": "GBP"},
                    ),
                ]
            ),
            principal=principal,
            session=session,
        )
    assert [f.sort_order for f in fields] == [1, 2]
    for field in fields:  # every `fld-new-N` placeholder replaced with a real id
        assert_public_id(field.uid)
    assert fields[1].conditions == {
        "conditional": {"field": "products", "operator": "contains", "value": "crypto"}
    }
    assert fields[1].options == {"currency": "GBP"}
    persisted_uid = fields[0].uid

    # a persisted uid survives a re-save (identity is stable across builder saves).
    async with session_factory() as session:
        resaved = await forms_router.set_form_fields(
            uid=uid,
            body=forms_router.SetFields(
                fields=[forms_router.FormFieldIn(uid=persisted_uid, field_key="name", label="Name")]
            ),
            principal=principal,
            session=session,
        )
    assert resaved[0].uid == persisted_uid

    # ...but a uid this form does NOT own is not honoured, it is replaced. The old check was
    # `uid.startswith("fld_")`, which accepted any string wearing the prefix -- including a field
    # id belonging to another form, which would then be stolen onto this one.
    async with session_factory() as session:
        other = await forms_router.create_form(
            body=forms_router.CreateForm(code=f"other_{uuid.uuid4().hex[:6]}", name="Other"),
            principal=principal,
            session=session,
        )
        stolen = await forms_router.set_form_fields(
            uid=other.uid,
            body=forms_router.SetFields(
                fields=[forms_router.FormFieldIn(uid=persisted_uid, field_key="name", label="Name")]
            ),
            principal=principal,
            session=session,
        )
    assert stolen[0].uid != persisted_uid
    assert_public_id(stolen[0].uid)

    # field_count reflects the current list; publish flips status.
    async with session_factory() as session:
        updated = await forms_router.update_form(
            uid=uid,
            body=forms_router.UpdateForm(name="Client Fact Find"),
            principal=principal,
            session=session,
        )
        published = await forms_router.publish_form(uid=uid, principal=principal, session=session)
        refetched = await forms_router.get_form(uid=uid, principal=principal, session=session)
    assert updated.name == "Client Fact Find"
    assert published.status == "published"
    assert refetched.field_count == 1

    # standalone response: answers round-trip, submitter name projected.
    async with session_factory() as session:
        resp = await forms_router.create_form_response(
            uid=uid,
            body=forms_router.FormResponseIn(answers={"name": "Jane"}),
            principal=principal,
            session=session,
        )
    assert resp.answers == {"name": "Jane"}
    assert resp.submitted_by == "Ada Staff"

    # ...and it shows up in the responses list, tagged 'standalone'.
    async with session_factory() as session:
        responses = await forms_router.list_form_responses(
            uid=uid, principal=principal, session=session
        )
    assert [r.uid for r in responses] == [resp.uid]
    assert responses[0].via == "standalone"
    assert responses[0].answers == {"name": "Jane"}
    assert responses[0].submitted_by == "Ada Staff"

    # delete = soft archive: drops off the list, but the row + fields stay resolvable.
    async with session_factory() as session:
        archived = await forms_router.delete_form(uid=uid, principal=principal, session=session)
    assert archived.status == "archived"
    async with session_factory() as session:
        listed_after = await forms_router.list_forms(principal=principal, session=session)
        still_there = await forms_router.get_form(uid=uid, principal=principal, session=session)
    assert uid not in {f.uid for f in listed_after}  # archived → hidden from the list
    assert still_there.status == "archived"  # ...but still directly readable
    assert still_there.field_count == 1  # fields preserved


@pytest.mark.asyncio
async def test_duplicate_code_conflicts(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["forms:write"])
    code = f"dupe_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        await forms_router.create_form(
            body=forms_router.CreateForm(code=code, name="One"),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await forms_router.create_form(
                body=forms_router.CreateForm(code=code, name="Two"),
                principal=principal,
                session=session,
            )
    assert err.value.status_code == 409


@pytest.mark.asyncio
async def test_archive_blocked_while_requested(session_factory, staff: dict[str, str]) -> None:
    # A form still referenced by an OPEN (pending) request item can't be archived — archiving it
    # would strand the client mid-fill. Once the item is no longer pending, archiving succeeds.
    principal = _principal(staff, ["forms:write"])
    code = f"inuse_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        form = await forms_router.create_form(
            body=forms_router.CreateForm(code=code, name="In Use"),
            principal=principal,
            session=session,
        )
        # Seed a pending complete_form request item pointing at the form (same tenant, via the RLS
        # session so tenant_id resolves from claims).
        req_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                    "VALUES (:u, auth.tenant_id(), :cid, auth.uid()) RETURNING id"
                ),
                {"u": f"req_{uuid.uuid4().hex[:8]}", "cid": int(staff["contact_id"])},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, form_uid) "
                "VALUES (:u, :rid, auth.tenant_id(), 'complete_form', 'Fill it', :form)"
            ),
            {"u": f"rqi_{uuid.uuid4().hex[:8]}", "rid": req_id, "form": form.uid},
        )

    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await forms_router.delete_form(uid=form.uid, principal=principal, session=session)
    assert err.value.status_code == 409

    # Close the request item; archiving now succeeds.
    async with session_factory() as session:
        await session.execute(
            text("UPDATE documents.request_items SET status = 'fulfilled' WHERE form_uid = :form"),
            {"form": form.uid},
        )
    async with session_factory() as session:
        archived = await forms_router.delete_form(
            uid=form.uid, principal=principal, session=session
        )
    assert archived.status == "archived"

    # Archived forms are hidden from the default list but visible with include_archived.
    async with session_factory() as session:
        default_list = await forms_router.list_forms(principal=principal, session=session)
        full_list = await forms_router.list_forms(
            include_archived=True, principal=principal, session=session
        )
    assert form.uid not in {f.uid for f in default_list}
    assert form.uid in {f.uid for f in full_list}

    # Un-archive brings it back as a draft; a second un-archive (not archived now) is a 409.
    async with session_factory() as session:
        revived = await forms_router.unarchive_form(
            uid=form.uid, principal=principal, session=session
        )
    assert revived.status == "draft"
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await forms_router.unarchive_form(uid=form.uid, principal=principal, session=session)
    assert err.value.status_code == 409


@pytest.mark.asyncio
async def test_responses_list_tags_request_fills(session_factory, staff: dict[str, str]) -> None:
    # A response created against a request item is tagged 'request' (vs 'standalone') and listed.
    principal = _principal(staff, ["forms:write", "forms:submit"])
    code = f"resp_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        form = await forms_router.create_form(
            body=forms_router.CreateForm(code=code, name="Resp"),
            principal=principal,
            session=session,
        )
        req_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                    "VALUES (:u, auth.tenant_id(), :cid, auth.uid()) RETURNING id"
                ),
                {"u": f"req_{uuid.uuid4().hex[:8]}", "cid": int(staff["contact_id"])},
            )
        ).scalar_one()
        item_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title, form_uid) "
                    "VALUES (:u, :rid, auth.tenant_id(), 'complete_form', 'Fill', :form) "
                    "RETURNING id"
                ),
                {"u": f"rqi_{uuid.uuid4().hex[:8]}", "rid": req_id, "form": form.uid},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO documents.form_responses "
                "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers, submitted_by) "
                "VALUES (:u, auth.tenant_id(), :iid, :form, :cid, "
                "  CAST(:answers AS jsonb), auth.uid())"
            ),
            {
                "u": f"frsp_{uuid.uuid4().hex[:8]}",
                "iid": item_id,
                "form": form.uid,
                "cid": int(staff["contact_id"]),
                "answers": '{"a": 1}',
            },
        )

    async with session_factory() as session:
        responses = await forms_router.list_form_responses(
            uid=form.uid, principal=principal, session=session
        )
    assert len(responses) == 1
    assert responses[0].via == "request"
    assert responses[0].answers == {"a": 1}


@pytest.mark.asyncio
async def test_publish_requires_fields(session_factory, staff: dict[str, str]) -> None:
    # A form with no fields can't be published (a client can't fill an empty form).
    principal = _principal(staff, ["forms:write"])
    async with session_factory() as session:
        form = await forms_router.create_form(
            body=forms_router.CreateForm(code=f"empty_{uuid.uuid4().hex[:6]}", name="Empty"),
            principal=principal,
            session=session,
        )
        with pytest.raises(HTTPException) as err:
            await forms_router.publish_form(uid=form.uid, principal=principal, session=session)
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_archived_form_publish_and_patch_guards(
    session_factory, staff: dict[str, str]
) -> None:
    # publish refuses an archived form (409), and a metadata PATCH can't flip status back (status
    # isn't a PATCH field) — reviving is only via the dedicated unarchive endpoint.
    principal = _principal(staff, ["forms:write"])
    async with session_factory() as session:
        form = await forms_router.create_form(
            body=forms_router.CreateForm(code=f"arch_{uuid.uuid4().hex[:6]}", name="Arch"),
            principal=principal,
            session=session,
        )
        await forms_router.set_form_fields(
            uid=form.uid,
            body=forms_router.SetFields(
                fields=[forms_router.FormFieldIn(field_key="name", label="Name")]
            ),
            principal=principal,
            session=session,
        )
        await forms_router.delete_form(uid=form.uid, principal=principal, session=session)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await forms_router.publish_form(uid=form.uid, principal=principal, session=session)
        assert err.value.status_code == 409
        # A metadata PATCH still works but leaves status archived (no revival path here).
        updated = await forms_router.update_form(
            uid=form.uid,
            body=forms_router.UpdateForm(name="Renamed"),
            principal=principal,
            session=session,
        )
    assert updated.name == "Renamed"
    assert updated.status == "archived"


@pytest.mark.asyncio
async def test_read_permission_required(session_factory, staff: dict[str, str]) -> None:
    # A principal with no forms permission is refused (require_permission), even though the session
    # itself is RLS-valid for the tenant.
    principal = _principal(staff, [])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await forms_router.list_forms(principal=principal, session=session)
    assert err.value.status_code == 403
