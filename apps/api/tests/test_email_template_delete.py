"""Deleting an email template — the act that replaced soft-archiving.

Archive was inherited from the document-template surface, where the endpoint named
``delete-template`` is itself only ``SET archived_at = now()``. Nothing had ever archived an email
template (zero rows in either environment), and archiving could not do the one thing a firm wanted:
``UNIQUE(tenant_id, code)`` has no archived predicate, so a mistake kept its own name for ever.

A template a firm asks to delete is DELETED. There is no in-use refusal: a template is named by
CODE and by no foreign key, so what still points at one is the author's business, and it is put to
them in the confirmation — which lists the notification rules that render it — rather than blocking
the delete on their behalf. The accepted consequences, pinned here so nobody rediscovers them as
bugs:

* a notification rule on a code with a shipped default keeps sending, on that default;
* a published journey whose ``send_email`` names the code has no such fallback and will park that
  step until someone re-points it;
* the destroyed row survives in ``audit.log``, which is the only recovery there is.
"""

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import templates_service as svc
from tests import email_support


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, prefix="x", name="XFirm", with_operator=True)


@pytest_asyncio.fixture
async def firm_session(db: dict[str, str], firm: dict[str, str]):
    """The firm router's own path: `authenticated` + claims, so the RLS policies are exercised."""
    opener, engine = email_support.session_opener(db, email_support.firm_claims(firm))
    yield opener
    await engine.dispose()


def _code(label: str) -> str:
    return f"{label}_{uuid.uuid4().hex[:6]}"


async def _create(session: AsyncSession, tenant_id: int, actor: str, code: str) -> dict[str, Any]:
    return await svc.create_template(
        session,
        tenant_id,
        actor,
        code=code,
        name="Chaser",
        category="notification",
        description=None,
        scope="firm",
    )


@pytest.mark.asyncio
async def test_deleting_a_template_frees_its_code_where_archiving_never_could(
    firm_session, firm: dict[str, str]
) -> None:
    """THE POINT OF THE FEATURE. A firm that mis-names a template gets the name back.

    The old archive could not give it back — the unique index is unconditional, so an archived row
    went on holding its code with no way to reach it from the UI.
    """
    tenant_id = int(firm["tenant_id"])
    actor = firm["user_uuid"]
    async with firm_session() as session:
        first = await _create(session, tenant_id, actor, "notification")

    # A SEPARATE session: the duplicate raises an IntegrityError, which aborts the transaction it
    # was raised in, so catching it here would roll the create above back with it.
    async with firm_session() as session:
        with pytest.raises(svc.DuplicateCode):
            await _create(session, tenant_id, actor, "notification")

    async with firm_session() as session:
        await svc.delete_template(session, tenant_id, first["uid"])

    async with firm_session() as session:
        again = await _create(session, tenant_id, actor, "notification")
    assert again["uid"] != first["uid"]


@pytest.mark.asyncio
async def test_delete_takes_the_whole_version_history_with_it(
    firm_session, firm: dict[str, str]
) -> None:
    """The cascade at ``0042:43`` is a deliberate choice, not an accident of schema design.

    One statement against the PARENT is also the only safe order: deleting a version first fails on
    the deferred ``current_published_version_id`` FK at COMMIT, where no handler can catch it.
    """
    tenant_id = int(firm["tenant_id"])
    async with firm_session() as session:
        row = await _create(session, tenant_id, firm["user_uuid"], "invite")
        uid = row["uid"]
        before = (
            await session.execute(
                text(
                    "SELECT count(*) FROM email.template_versions v "
                    "JOIN email.templates t ON t.id = v.template_id WHERE t.uid = :u"
                ),
                {"u": uid},
            )
        ).scalar_one()
        assert before >= 1

    async with firm_session() as session:
        await svc.delete_template(session, tenant_id, uid)

    async with firm_session() as session:
        assert (
            await session.execute(
                text("SELECT count(*) FROM email.templates WHERE uid = :u"), {"u": uid}
            )
        ).scalar_one() == 0
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM email.template_versions v WHERE v.template_id NOT IN "
                    "(SELECT id FROM email.templates)"
                )
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_second_delete_is_not_found_not_a_cheerful_success(
    firm_session, firm: dict[str, str]
) -> None:
    """Archive answered 200 for an already-archived template (its UPDATE simply matched nothing and
    it returned the row). A delete must not inherit that: gone is gone, and a caller repeating the
    request has to be able to tell the difference between "I deleted it" and "it was not there"."""
    tenant_id = int(firm["tenant_id"])
    async with firm_session() as session:
        row = await _create(session, tenant_id, firm["user_uuid"], "password_reset")

    async with firm_session() as session:
        await svc.delete_template(session, tenant_id, row["uid"])

    async with firm_session() as session:
        with pytest.raises(svc.TemplateNotFound):
            await svc.delete_template(session, tenant_id, row["uid"])
        with pytest.raises(svc.TemplateNotFound):
            await svc.delete_template(session, tenant_id, "eml_never_existed")


@pytest.mark.asyncio
async def test_a_firm_invented_code_with_nothing_pointing_at_it_just_deletes(
    firm_session, firm: dict[str, str]
) -> None:
    """The abandoned experiment — a template called ``test`` — is the case delete exists for.

    Having no built-in default behind the code is not a reason to refuse on its own: nothing but a
    journey can send such a code (``rules_service`` refuses to store one outside the catalogue), so
    a template nothing points at is exactly as safe to delete as it looks. Refusing it made the one
    template a firm most wants gone the one template it could not remove.
    """
    tenant_id = int(firm["tenant_id"])
    async with firm_session() as session:
        row = await _create(session, tenant_id, firm["user_uuid"], _code("test"))

    async with firm_session() as session:
        gone = await svc.delete_template(session, tenant_id, row["uid"])
    assert gone["uid"] == row["uid"]

    async with firm_session() as session:
        assert await svc.fetch(session, tenant_id, row["uid"]) is None


@pytest.mark.asyncio
async def test_a_notification_rule_does_not_block_the_delete(
    firm_session, firm: dict[str, str]
) -> None:
    """The firm's own templates are precisely the ones its rules point at, so blocking on a rule
    would make the feature useless for the case it exists for.

    A rule naming a code with a shipped default falls back to it — a complete, branded, correctly
    wrapped email, exactly what archiving the same template already produced. The author is told
    which rules those are in the confirmation, and decides.
    """
    tenant_id = int(firm["tenant_id"])
    async with firm_session() as session:
        row = await _create(session, tenant_id, firm["user_uuid"], "document_sent")

    async with firm_session() as session:
        gone = await svc.delete_template(session, tenant_id, row["uid"])
    assert gone["code"] == "document_sent"

    async with firm_session() as session:
        assert await svc.fetch(session, tenant_id, row["uid"]) is None


@pytest.mark.asyncio
async def test_the_destroyed_row_survives_in_the_audit_trail(
    firm_session, firm: dict[str, str], super_engine: Engine
) -> None:
    """The only thing that makes an irreversible delete defensible.

    ``audit.record`` keeps the whole row, so a template deleted in error is recoverable by someone
    with database access. Nothing in the product surfaces it, which is why the confirmation has to
    be honest about the act rather than lean on this.
    """
    tenant_id = int(firm["tenant_id"])
    async with firm_session() as session:
        row = await _create(session, tenant_id, firm["user_uuid"], "request_raised")

    with super_engine.begin() as conn:
        # The template's internal id, because a VERSION's audit row names `template_id` and its own
        # uid — never the template's. Filtering both on the template uid finds only the header and
        # reads as "the cascade is not audited".
        template_id = conn.execute(
            text("SELECT id FROM email.templates WHERE uid = :u"), {"u": row["uid"]}
        ).scalar_one()

    async with firm_session() as session:
        await svc.delete_template(session, tenant_id, row["uid"])

    with super_engine.begin() as conn:
        actions = set(
            conn.execute(
                text(
                    "SELECT action FROM audit.log WHERE "
                    "  (action = 'email.templates.delete' AND detail::text LIKE :needle) "
                    "  OR (action = 'email.template_versions.delete' "
                    "      AND (detail -> 'row' ->> 'template_id')::int = :tid)"
                ),
                {"needle": f"%{row['uid']}%", "tid": template_id},
            ).scalars()
        )
        kept = conn.execute(
            text(
                "SELECT detail -> 'row' ->> 'code' FROM audit.log "
                "WHERE action = 'email.templates.delete' AND detail::text LIKE :needle"
            ),
            {"needle": f"%{row['uid']}%"},
        ).scalar_one()
    # The cascade is audited too, so the version history is recoverable and not merely the header.
    assert actions == {"email.templates.delete", "email.template_versions.delete"}
    assert kept == "request_raised", (
        "the destroyed row itself must be in the record, not just its id"
    )


# ── The gaps the seven above leave ────────────────────────────────────────────────────────────────
# Each is a way the delete could go wrong that the existing cases cannot see: the RLS boundary, the
# second of the two fatal rails, and the deferred FK the docstring warns about but no case exercises
# on a template that actually HAS a published pointer.


@pytest.mark.asyncio
async def test_another_firms_template_is_not_found_rather_than_deleted(
    firm_session, db: dict[str, str], super_engine: Engine
) -> None:
    """THE RLS BOUNDARY. `delete_template` asks `_template_id` first precisely because "absent" and
    "another tenant's" are the same zero under RLS — so the refusal must be 404 either way, and the
    other firm's row must still be there afterwards."""
    other = email_support.make_firm(super_engine, prefix="y", name="YFirm", with_operator=True)
    other_opener, other_engine = email_support.session_opener(db, email_support.firm_claims(other))
    try:
        async with other_opener() as session:
            theirs = await _create(session, int(other["tenant_id"]), other["user_uuid"], "invite")

        # The FIRST firm's session, asking for the SECOND firm's uid.
        async with firm_session() as session:
            with pytest.raises(svc.TemplateNotFound):
                await svc.delete_template(session, int(other["tenant_id"]), theirs["uid"])
        async with firm_session() as session:
            with pytest.raises(svc.TemplateNotFound):
                await svc.delete_template(session, 999_999, theirs["uid"])

        # Still theirs, untouched.
        async with other_opener() as session:
            assert (
                await session.execute(
                    text("SELECT count(*) FROM email.templates WHERE uid = :u"),
                    {"u": theirs["uid"]},
                )
            ).scalar_one() == 1
    finally:
        await other_engine.dispose()


@pytest.mark.asyncio
async def test_deleting_a_template_that_has_a_published_pointer_survives_the_deferred_fk(
    firm_session, firm: dict[str, str]
) -> None:
    """THE TRAP THE DOCSTRING NAMES. `current_published_version_id` is a circular FK, DEFERRABLE
    INITIALLY DEFERRED, so it is checked at COMMIT — outside any handler. Every case above
    deletes a template whose pointer is null; this one publishes first, so the pointer is SET and
    the commit is what has to survive. A regression here raises after the body has already passed.
    """
    tenant_id = int(firm["tenant_id"])
    actor = firm["user_uuid"]
    async with firm_session() as session:
        row = await _create(session, tenant_id, actor, "invite")
        uid = row["uid"]
        # A fresh template has no subject and publish rightly refuses one, so fill the draft first:
        # the point of this case is the COMMIT, not the gates covered elsewhere.
        await svc.save_version(
            session,
            tenant_id,
            actor,
            uid,
            subject="Your account is ready",
            preheader="Set your password to sign in.",
            schema={
                "html": "<p>Welcome aboard.</p>",
                "css": "",
                "source": "<p>Welcome aboard.</p>",
            },
            scope="firm",
        )
        versions = await svc.list_versions(session, tenant_id, uid)
        await svc.publish_version(session, tenant_id, actor, uid, versions[0]["uid"], scope="firm")

    async with firm_session() as session:
        pointer = (
            await session.execute(
                text(
                    "SELECT current_published_version_id FROM email.templates "
                    "WHERE uid = :u AND tenant_id = :t"
                ),
                {"u": uid, "t": tenant_id},
            )
        ).scalar_one()
        assert pointer is not None, "the test must delete a template whose pointer is SET"

    # The commit is the assertion: a deferred-FK violation raises on exit from this block.
    async with firm_session() as session:
        await svc.delete_template(session, tenant_id, uid)

    async with firm_session() as session:
        assert (
            await session.execute(
                text("SELECT count(*) FROM email.templates WHERE uid = :u"), {"u": uid}
            )
        ).scalar_one() == 0
