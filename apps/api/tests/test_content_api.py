"""Staff CRUD + publish for /content, and the client read filter on /crm/content."""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, InternalError

from app.auth import Principal
from app.ids import new_id
from app.routers import content as rt
from tests import email_support

WRITE = ["content:write"]


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, prefix="c")


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff, perms=WRITE))
    yield opener
    await engine.dispose()


def _principal(staff: dict[str, str]) -> Principal:
    return email_support.principal(staff, perms=WRITE)


@pytest.mark.asyncio
async def test_create_save_publish_lifecycle(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_item(
            body=rt.CreateContentItem(kind="article", title="Pensions vs ISAs"),
            principal=principal,
            session=session,
        )
    assert created.status == "draft"
    assert created.slug.startswith("pensions")
    uid = created.uid

    async with session_factory() as session:
        saved = await rt.update_item(
            uid=uid,
            body=rt.UpdateContentItem.model_validate(
                {
                    "summary": "Tax relief makes a difference.",
                    "audience": "individual",
                    "service_types": ["execution_only", "discretionary"],
                    "schema": {
                        "html": "<h1>Hello</h1><p>Body</p><script>alert(1)</script>",
                        "css": "",
                    },
                }
            ),
            principal=principal,
            session=session,
        )
    assert saved.summary is not None
    assert saved.audience == "individual"

    async with session_factory() as session:
        published = await rt.publish_item(uid=uid, principal=principal, session=session)
    assert published.status == "published"
    assert published.body_html is not None
    assert "<script" not in published.body_html.lower()
    assert "Hello" in published.body_html
    assert published.published_at is not None


@pytest.mark.asyncio
async def test_publish_refuses_empty_article(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_item(
            body=rt.CreateContentItem(kind="news", title="Market note"),
            principal=principal,
            session=session,
        )
        uid = created.uid
    with pytest.raises(HTTPException) as exc:
        async with session_factory() as session:
            await rt.publish_item(uid=uid, principal=principal, session=session)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_client_list_hides_draft_and_wrong_audience(
    session_factory, staff: dict[str, str], db: dict[str, str], super_engine: Engine
) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        draft = await rt.create_item(
            body=rt.CreateContentItem(kind="news", title="Draft news"),
            principal=principal,
            session=session,
        )
        live = await rt.create_item(
            body=rt.CreateContentItem(kind="news", title="Live news"),
            principal=principal,
            session=session,
        )
        await rt.update_item(
            uid=live.uid,
            body=rt.UpdateContentItem.model_validate(
                {
                    "audience": "organisation",
                    "schema": {"html": "<p>For trusts</p>", "css": ""},
                }
            ),
            principal=principal,
            session=session,
        )
        await rt.publish_item(uid=live.uid, principal=principal, session=session)
        all_aud = await rt.create_item(
            body=rt.CreateContentItem(kind="article", title="Everyone"),
            principal=principal,
            session=session,
        )
        await rt.update_item(
            uid=all_aud.uid,
            body=rt.UpdateContentItem.model_validate({"schema": {"html": "<p>Hi</p>", "css": ""}}),
            principal=principal,
            session=session,
        )
        await rt.publish_item(uid=all_aud.uid, principal=principal, session=session)

    # An individual execution-only client: org-only news is hidden; the all-audience article is not.
    with super_engine.connect() as c:
        contact_id = c.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                "VALUES (:t, :uid, 'individual', 'Client', 'execution_only') RETURNING id"
            ),
            {"t": int(staff["tenant_id"]), "uid": new_id()},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :c, CAST(:u AS uuid), 'self')"
            ),
            {"t": int(staff["tenant_id"]), "c": contact_id, "u": staff["user_uuid"]},
        )
        c.commit()

    claims = email_support.firm_claims(staff, perms=WRITE)
    claims["portal"] = "client"
    claims["tenant_ids"] = []
    claims["contact_ids"] = [int(contact_id)]
    opener, engine = email_support.session_opener(db, claims)
    try:
        async with opener() as session:
            listed = await rt.list_crm_content(
                kind=None,
                category=None,
                q=None,
                limit=25,
                offset=0,
                contact_id=int(contact_id),
                session=session,
            )
        uids = {i.uid for i in listed.items}
        assert draft.uid not in uids
        assert live.uid not in uids
        assert all_aud.uid in uids
        async with opener() as session:
            with pytest.raises(HTTPException) as exc:
                await rt.get_crm_content(uid=draft.uid, contact_id=int(contact_id), session=session)
            assert exc.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_versions_are_immutable(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_item(
            body=rt.CreateContentItem(kind="article", title="Immutable"),
            principal=principal,
            session=session,
        )
        await rt.update_item(
            uid=created.uid,
            body=rt.UpdateContentItem.model_validate({"schema": {"html": "<p>One</p>", "css": ""}}),
            principal=principal,
            session=session,
        )
        await rt.publish_item(uid=created.uid, principal=principal, session=session)

    async with session_factory() as session:
        with pytest.raises((IntegrityError, InternalError)):
            await session.execute(text("UPDATE content.item_versions SET body_html = 'nope'"))
            await session.flush()

    async with session_factory() as session:
        html = (
            await session.execute(text("SELECT body_html FROM content.item_versions LIMIT 1"))
        ).scalar_one()
    assert "nope" not in html
    assert "One" in html
