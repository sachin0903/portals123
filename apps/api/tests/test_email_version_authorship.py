"""`email.template_versions.authored_by_scope` (0051) — can a firm tell when GRAPHENE edited its
template?

`created_by`/`published_by` resolve to a PERSON, which cannot answer that: an operator acting on a
firm's behalf writes through the same shared service as the firm's own staff. So the CALLING SIDE
stamps the scope at write time.

Pinned: firm writes stamp 'firm' and admin-door writes stamp 'operations' on a real firm's
tenant, including the INSERT branches (roll + restore) and not just the in-place one; ops
publishing a firm's draft is an operations act; the stamp is sticky at 'operations' so a later
firm autosave cannot launder it; THE POINT — a labelled version is still readable by the firm
under RLS, because a version the firm cannot see is worse than an unlabelled one; seeding stamps
'operations'; pre-0051 rows read back as None, never back-dated to a false 'firm' claim.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text

from app.email import templates_service as svc
from app.routers import admin
from app.routers import email_templates as et
from tests import email_support

CONTENT = {"html": "<p>Hello {{ client.first_name }}</p>", "css": ".x{color:#0f766e}"}


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, prefix="a", name="AFirm", with_operator=True)


@pytest_asyncio.fixture
async def firm_session(db: dict[str, str], firm: dict[str, str]):
    """The firm router's own path: `authenticated` + claims, so the RLS policies are exercised."""
    opener, engine = email_support.session_opener(db, email_support.firm_claims(firm))
    yield opener
    await engine.dispose()


@pytest_asyncio.fixture
async def ops_session(db: dict[str, str], firm: dict[str, str]):
    """The admin door: `service_role` + the operator's claims (mirrors db.admin_session), so
    `_operator_uid` (auth.uid()) resolves and it can be driven cross-tenant."""
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims(firm["operator_uuid"]), role="service_role"
    )
    yield opener
    await engine.dispose()


OPS = email_support.ops_principal()


async def _ops_create(session, tenant_id: int, label: str):
    return await admin.admin_create_email_template(
        tenant_id=tenant_id,
        body=admin.CreateEmailTemplate(code=f"{label}_{uuid.uuid4().hex[:6]}", name="Ops"),
        session=session,
        principal=OPS,
    )


async def _ops_save(session, tenant_id: int, uid: str, subject: str):
    return await admin.admin_save_email_template_version(
        tenant_id=tenant_id,
        uid=uid,
        body=admin.SaveEmailVersion(subject=subject, schema_content=CONTENT),
        session=session,
        principal=OPS,
    )


async def _svc_author(session, tenant_id: int, actor: str) -> tuple[str, dict]:
    """A template + a saved v1 through the shared service (the firm side). Returns (tpl uid, v1)."""
    tpl = await svc.create_template(
        session,
        tenant_id,
        actor,
        code=f"authored_{uuid.uuid4().hex[:6]}",
        name="Source",
        category="notification",
        description=None,
        scope="firm",
    )
    version = await svc.save_version(
        session,
        tenant_id,
        actor,
        tpl["uid"],
        subject="Source subject",
        preheader="",
        schema=CONTENT,
        scope="firm",
    )
    return str(tpl["uid"]), version


async def _scope_of(session, vid: str) -> str | None:
    """The stored column, read straight from the table (not the projection)."""
    return (
        await session.execute(
            text("SELECT authored_by_scope FROM email.template_versions WHERE uid = :v"),
            {"v": vid},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_firm_save_publish_and_restore_stamp_firm(firm_session, firm: dict[str, str]) -> None:
    principal = email_support.principal(firm)
    async with firm_session() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=f"firmside_{uuid.uuid4().hex[:6]}", name="Firm"),
            principal=principal,
            session=session,
        )
        first = await et.list_email_template_versions(
            uid=created.uid, principal=principal, session=session
        )
        assert first[0].authored_by_scope == "firm"

        saved = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(subject="Hello", preheader="Pre", schema_content=CONTENT),
            principal=principal,
            session=session,
        )
        assert saved.authored_by_scope == "firm"
        assert await _scope_of(session, saved.uid) == "firm"

        published = await et.publish_email_template_version(
            uid=created.uid, vid=saved.uid, principal=principal, session=session
        )
        restored = await et.restore_email_template_version(
            uid=created.uid, vid=saved.uid, principal=principal, session=session
        )
    assert published.authored_by_scope == "firm"
    assert restored.authored_by_scope == "firm"


@pytest.mark.asyncio
async def test_admin_door_save_publish_roll_and_restore_stamp_operations(
    ops_session, firm: dict[str, str]
) -> None:
    tenant_id = int(firm["tenant_id"])
    async with ops_session() as session:
        created = await _ops_create(session, tenant_id, "opsside")
        versions = await admin.admin_list_email_template_versions(
            tenant_id=tenant_id, uid=created.uid, session=session, principal=OPS
        )
        assert versions[0].authored_by_scope == "operations"

        saved = await _ops_save(session, tenant_id, created.uid, "From us")
        assert saved.authored_by_scope == "operations"
        assert await _scope_of(session, saved.uid) == "operations"

        published = await admin.admin_publish_email_template_version(
            tenant_id=tenant_id, uid=created.uid, vid=saved.uid, session=session, principal=OPS
        )
        assert published.authored_by_scope == "operations"

        # Head is PUBLISHED, so this takes the INSERT branch; the in-place one would be vacuous.
        rolled = await _ops_save(session, tenant_id, created.uid, "Second pass")
        assert rolled.uid != saved.uid and rolled.version_number == 2
        assert await _scope_of(session, rolled.uid) == "operations"

        restored = await admin.admin_restore_email_template_version(
            tenant_id=tenant_id, uid=created.uid, vid=saved.uid, session=session, principal=OPS
        )
        assert restored.uid not in {saved.uid, rolled.uid}
        assert restored.authored_by_scope == "operations"


@pytest.mark.asyncio
async def test_ops_publishing_a_firm_draft_marks_the_version_operations(
    firm_session, ops_session, firm: dict[str, str]
) -> None:
    """The firm wrote the content; GRAPHENE decided to make it live. `published_by` names a
    person, so without the stamp the firm could not tell that decision was ours."""
    async with firm_session() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=f"opspub_{uuid.uuid4().hex[:6]}", name="Firm drafted"),
            principal=email_support.principal(firm),
            session=session,
        )
        draft = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(subject="Firm subject", schema_content=CONTENT),
            principal=email_support.principal(firm),
            session=session,
        )
    assert draft.authored_by_scope == "firm"

    async with ops_session() as session:
        published = await admin.admin_publish_email_template_version(
            tenant_id=int(firm["tenant_id"]),
            uid=created.uid,
            vid=draft.uid,
            session=session,
            principal=OPS,
        )
    assert published.authored_by_scope == "operations"


@pytest.mark.asyncio
async def test_a_firm_save_never_launders_an_operations_stamp(
    firm_session, ops_session, firm: dict[str, str]
) -> None:
    """Ops writes a draft; the firm autosaves over it in the same 30-minute bucket (in place, no
    new version). Sticky at 'operations' — otherwise the one write the firm most needs to see is the
    one a subsequent autosave erases."""
    tenant_id = int(firm["tenant_id"])
    async with ops_session() as session:
        created = await _ops_create(session, tenant_id, "sticky")
        ops_draft = await _ops_save(session, tenant_id, created.uid, "Ours")

    async with firm_session() as session:
        firm_save = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(subject="Theirs", schema_content=CONTENT),
            principal=email_support.principal(firm),
            session=session,
        )
    assert firm_save.uid == ops_draft.uid  # same version row
    assert firm_save.subject == "Theirs"
    assert firm_save.authored_by_scope == "operations"


@pytest.mark.asyncio
async def test_ops_authored_version_stays_readable_by_the_firm(
    firm_session, ops_session, firm: dict[str, str]
) -> None:
    """THE POINT. An ops-authored version carries the FIRM's tenant_id, so 0042's policies
    (which predicate on tenant lineage, not authorship) still admit it: the firm must see the
    row — and its content — labelled, through both read shapes and the pydantic model the routers
    share."""
    tenant_id = int(firm["tenant_id"])
    async with ops_session() as session:
        created = await _ops_create(session, tenant_id, "visible")
        ops_version = await _ops_save(session, tenant_id, created.uid, "On your behalf")

    async with firm_session() as session:  # the FIRM's own RLS-bound session, its own router
        listed = await et.list_email_template_versions(
            uid=created.uid, principal=email_support.principal(firm), session=session
        )
        detail = await et.get_email_template_version(
            uid=created.uid,
            vid=ops_version.uid,
            principal=email_support.principal(firm),
            session=session,
        )
        svc_rows = await svc.list_versions(session, tenant_id, created.uid)
        svc_one = await svc.get_version(session, tenant_id, created.uid, ops_version.uid)

    assert [v.uid for v in listed] == [ops_version.uid]  # visible, not filtered away
    assert listed[0].authored_by_scope == "operations"  # and labelled
    assert detail.authored_by_scope == "operations"
    assert detail.subject == "On your behalf"  # the content is readable too, not just the row
    assert svc_rows[0]["authored_by_scope"] == "operations"
    assert svc_one["authored_by_scope"] == "operations"


@pytest.mark.asyncio
async def test_seeding_stamps_operations_whatever_the_source_said(
    super_engine: Engine, service_session, firm: dict[str, str]
) -> None:
    """A seeded or cloned preset was put there by Graphene; the destination firm never wrote it,
    even when the SOURCE version was firm-authored."""
    tenant_id = int(firm["tenant_id"])
    assert await svc.load_defaults(service_session, tenant_id, firm["operator_uuid"]) > 0
    scopes = (
        await service_session.execute(
            text(
                "SELECT DISTINCT authored_by_scope FROM email.template_versions WHERE tenant_id=:t"
            ),
            {"t": tenant_id},
        )
    ).scalars()
    assert set(scopes) == {"operations"}

    dst = email_support.make_firm(super_engine, prefix="d", name="DFirm")
    src_tpl, src_v = await _svc_author(service_session, tenant_id, firm["user_uuid"])
    assert src_v["authored_by_scope"] == "firm"
    await svc.publish_version(
        service_session, tenant_id, firm["user_uuid"], src_tpl, src_v["uid"], scope="firm"
    )

    await svc.clone_published(
        service_session,
        src_tenant_id=tenant_id,
        dst_tenant_id=int(dst["tenant_id"]),
        actor_id=firm["operator_uuid"],
    )
    dst_scopes = (
        await service_session.execute(
            text(
                "SELECT DISTINCT authored_by_scope FROM email.template_versions WHERE tenant_id=:t"
            ),
            {"t": int(dst["tenant_id"])},
        )
    ).scalars()
    assert set(dst_scopes) == {"operations"}


@pytest.mark.asyncio
async def test_a_pre_0051_row_reads_back_as_unknown(service_session, firm: dict[str, str]) -> None:
    """The column is nullable with NO default precisely so historical rows stay honest: None,
    never back-dated to 'firm' — a false claim about exactly the rows the admin door had written."""
    tenant_id = int(firm["tenant_id"])
    tpl_uid, _v = await _svc_author(service_session, tenant_id, firm["user_uuid"])
    await service_session.execute(
        text(
            "UPDATE email.template_versions SET authored_by_scope = NULL WHERE tenant_id = :t "
            "AND template_id = (SELECT id FROM email.templates WHERE uid = :u)"
        ),
        {"t": tenant_id, "u": tpl_uid},
    )
    rows = await svc.list_versions(service_session, tenant_id, tpl_uid)
    assert rows[0]["authored_by_scope"] is None
    assert et.EmailVersionOut(**rows[0]).authored_by_scope is None


def test_the_vocabulary_is_closed(super_engine: Engine, firm: dict[str, str]) -> None:
    """The CHECK is the backstop under the typed parameter — no service call is willing to write
    a bad value, which is why this is a raw probe."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        template_id = int(
            c.execute(
                text(
                    "INSERT INTO email.templates (uid, tenant_id, code, name, created_by) "
                    "VALUES (:uid, :t, :code, 'Checked', :actor) RETURNING id"
                ),
                {
                    "uid": f"eml_chk_{s}",
                    "t": int(firm["tenant_id"]),
                    "code": f"check_{s}",
                    "actor": firm["user_uuid"],
                },
            ).scalar_one()
        )
        with pytest.raises(Exception, match="authored_by_scope"):
            c.execute(
                text(
                    "INSERT INTO email.template_versions (uid, tenant_id, template_id, "
                    "  version_number, created_by, authored_by_scope) "
                    "VALUES (:uid, :t, :tid, 1, :actor, 'graphene')"
                ),
                {
                    "uid": f"emlv_chk_{s}",
                    "t": int(firm["tenant_id"]),
                    "tid": template_id,
                    "actor": firm["user_uuid"],
                },
            )
