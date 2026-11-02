"""Endpoint behaviour for the /email-templates router: the draft/publish/version lifecycle, and
which version the SEND path resolves.

Drives the router coroutines against a real `authenticated` session (role + claims like
db.py::tenant_session), so the SQL, the version state machine and the pydantic shaping are exercised
under RLS — not just the policies. The publish GATES themselves live in
`test_email_publish_guards.py`; the resolve rules (published wins, draft never sends, archived stops
overriding) live here because they are one join in real SQL that no fake session can exercise.
"""

import types
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine

from app.email import render as email_render
from app.email import templates_service as svc
from app.email.render import render_published_email
from app.routers import email_templates as et
from tests import email_support

#: The same context feeds the assertion and the call, so the comparison is about WHICH branch ran.
_FALLBACK_CONTEXT: dict[str, object] = {
    "brand": {
        "name": "Ridley & Co",
        "logo_url": None,
        "accent": "#0f766e",
        "support_email": None,
    },
    "first_name": "Bo",
    "portal_label": "Client Portal",
    "set_password_url": "https://portal.ridley.test/set-password?token=t",
}

CONTENT = {"html": "<p>Hi {{ client.first_name }}</p>", "css": ".x{color:#0f766e}"}


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine)


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff))
    yield opener
    await engine.dispose()


@pytest.mark.asyncio
async def test_email_template_version_lifecycle(session_factory, staff: dict[str, str]) -> None:
    """create → autosave in place → publish → save again (rolls v2) → restore v1 (a fresh v3 draft
    carrying v1's content, never overwriting and never auto-publishing)."""
    principal = email_support.principal(staff)
    code = f"invite_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=code, name="Invitation", category="transactional"),
            principal=principal,
            session=session,
        )
    assert created.version_count == 1
    assert created.current_version_number == 1
    assert created.current_version_status == "draft"
    assert created.category == "transactional"
    assert created.created_by == "Cara Staff"
    uid = created.uid

    async with session_factory() as session:
        listed = await et.list_email_templates(principal=principal, session=session)
        got = await et.get_email_template(uid=uid, principal=principal, session=session)
    assert uid in {t.uid for t in listed}
    assert got.code == code

    async with session_factory() as session:
        v1 = await et.save_email_template_version(
            uid=uid,
            body=et.SaveEmailVersion(
                subject="Set your password",
                preheader="Activate your account",
                schema_content=CONTENT,
            ),
            principal=principal,
            session=session,
        )
        again = await et.get_email_template(uid=uid, principal=principal, session=session)
    assert (v1.version_number, v1.status) == (1, "draft")
    assert v1.subject == "Set your password"
    assert v1.preheader == "Activate your account"
    assert v1.schema_content == CONTENT
    assert again.version_count == 1  # in-place, not a new version

    async with session_factory() as session:
        published = await et.publish_email_template_version(
            uid=uid, vid=v1.uid, principal=principal, session=session
        )
        after = await et.get_email_template(uid=uid, principal=principal, session=session)
    assert published.status == "published"
    assert after.current_version_status == "published"

    async with session_factory() as session:
        v2 = await et.save_email_template_version(
            uid=uid,
            body=et.SaveEmailVersion(
                subject="Subject two", schema_content={"html": "<p>Version two</p>", "css": ""}
            ),
            principal=principal,
            session=session,
        )
        restored = await et.restore_email_template_version(
            uid=uid, vid=v1.uid, principal=principal, session=session
        )
    assert (v2.version_number, v2.status) == (2, "draft")
    assert (restored.version_number, restored.status) == (3, "draft")
    assert restored.subject == "Set your password"
    assert restored.preheader == "Activate your account"
    assert restored.schema_content == CONTENT


@pytest.mark.asyncio
async def test_the_gates_and_the_404s(session_factory, staff: dict[str, str]) -> None:
    """A principal holding neither `email_templates:read` nor write is refused on both doors, and an
    unknown uid/vid is a 404 rather than a 500 or a silent empty answer."""
    writer = email_support.principal(staff)
    nobody = email_support.principal(staff, [])
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=f"gate_{uuid.uuid4().hex[:6]}", name="Locked"),
            principal=writer,
            session=session,
        )

    async with session_factory() as session:
        for call in (
            lambda: et.list_email_templates(principal=nobody, session=session),
            lambda: et.preview_email_template(
                uid=created.uid,
                body=et.PreviewEmail(html="<p>hi</p>"),
                principal=nobody,
                session=session,
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                await call()
            assert ei.value.status_code == 403

        with pytest.raises(HTTPException) as ei:
            await et.restore_email_template_version(
                uid=created.uid, vid="emlv_nope", principal=writer, session=session
            )
        assert ei.value.status_code == 404
        with pytest.raises(HTTPException) as ei:
            await et.preview_email_template(
                uid="eml_does_not_exist",
                body=et.PreviewEmail(html="<p>hi</p>"),
                principal=writer,
                session=session,
            )
        assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_the_default_endpoint_hands_the_editor_the_shipped_preheader(
    session_factory, staff: dict[str, str]
) -> None:
    """ "Load default" returned only subject/html/css, so an author who loaded the default and
    published it shipped an override with NO inbox preview line — while the shipped default it
    replaced has one. Losing content by accepting the default is the same defect class as the
    `request_raised` pluralisation."""
    from app.email import defaults as email_defaults

    principal = email_support.principal(staff)
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code="invite", name="Invitation"),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        out = await et.email_template_default(uid=created.uid, principal=principal, session=session)
    assert out.preheader == email_defaults.preheader_for("invite")
    assert out.preheader, "the editor is still being handed a blank preheader"
    assert out.subject and out.html


@pytest.mark.asyncio
async def test_a_draft_only_template_never_reaches_a_client(
    session_factory, staff: dict[str, str]
) -> None:
    """THE fail-safe of the authoring surface: an unpublished edit must not send.

    The rule lives in one join — `v.id = t.current_published_version_id` — which the hermetic
    fallback tests cannot exercise (their fake session answers every SELECT with the same row). A
    draft is the state an author is in most of the time, and half-written copy reaching a real
    client is unrecoverable. The code is a real catalogue one, so the fallback is the SHIPPED
    default."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code="invite", name="Work in progress"),
            principal=principal,
            session=session,
        )
        await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(
                subject="HALF WRITTEN — do not send",
                preheader="draft preheader",
                schema_content={"html": "<p>TODO: finish this paragraph</p>", "css": ""},
            ),
            principal=principal,
            session=session,
        )

    async with session_factory() as session:
        assert (
            await render_published_email(
                session, tenant_id=int(staff["tenant_id"]), code="invite", context={}
            )
            is None
        )
        subject, html = await email_render.render_or_default(
            session, tenant_id=int(staff["tenant_id"]), code="invite", context=_FALLBACK_CONTEXT
        )
    assert (subject, html) == email_render.render_default(
        "invite", _FALLBACK_CONTEXT, asset_origin=None
    )
    assert "HALF WRITTEN" not in subject and "TODO" not in html


@pytest.mark.asyncio
async def test_a_deleted_template_stops_overriding_the_shipped_default(
    session_factory, staff: dict[str, str]
) -> None:
    """Deleting is how a firm withdraws an override, and the send must not break when it does.

    This is the case that makes deleting a CATALOGUE code safe: the row and its versions go, the
    resolver finds no override, and the recipient gets the shipped default — a complete, branded
    email rather than nothing. A code the catalogue does not know has no such floor, which is why
    `template_refs` refuses that delete instead."""
    principal = email_support.principal(staff)
    code = "password_reset"  # a catalogue code — see the draft test above
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=code, name="Retired"),
            principal=principal,
            session=session,
        )
        v = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(
                subject="The firm's own subject",
                schema_content={"html": "<p>The firm's own wording</p>", "css": ""},
            ),
            principal=principal,
            session=session,
        )
        await et.publish_email_template_version(
            uid=created.uid, vid=v.uid, principal=principal, session=session
        )

    async with session_factory() as session:  # while live, it wins
        live = await render_published_email(
            session, tenant_id=int(staff["tenant_id"]), code=code, context={}
        )
    assert live is not None and "The firm's own wording" in live.html

    async with session_factory() as session:
        await et.delete_email_template(uid=created.uid, principal=principal, session=session)
    async with session_factory() as session:
        subject, html = await email_render.render_or_default(
            session, tenant_id=int(staff["tenant_id"]), code=code, context=_FALLBACK_CONTEXT
        )
    assert (subject, html) == email_render.render_default(
        code, _FALLBACK_CONTEXT, asset_origin=None
    ), "an archived template is still overriding the shipped default"
    assert "The firm's own wording" not in html


@pytest.mark.asyncio
async def test_the_editor_canvas_is_exactly_what_the_send_path_reads(
    session_factory, staff: dict[str, str]
) -> None:
    """The round trip: the `schema` the editor saved is the html AND css the send path renders — not
    a re-derived or lossy copy — merged, inlined, and byte-identical to the author's Preview of the
    same canvas. Only `html` and `css` are read out of `schema` (`gjs` and anything else is editor
    state, not content), so a change that started sending a reconstruction would show up here."""
    from app.email import preview as email_preview
    from app.email.transport import Brand

    principal = email_support.principal(staff)
    code = "document_sent"  # a real catalogue code, so the sample context is the send-site one
    brand = Brand(name="Ridgeway Wealth", accent="#0f766e", support_email="hi@ridgeway.test")
    canvas = {
        "html": (
            '<div class="cnv"><h1>Hi {{ first_name }}</h1>'
            "<p>{{ document_name }} from {{ sent_by }}</p></div>"
        ),
        "css": ".cnv{padding:19px;border-top:3px solid #abcdef}",
        "gjs": {"components": [{"tagName": "div", "attributes": {"class": "cnv"}}]},
    }
    subject, preheader = "{{ brand.name }}: {{ document_name }}", "From {{ sent_by }}"

    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=code, name="Document sent"),
            principal=principal,
            session=session,
        )
        v = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(subject=subject, preheader=preheader, schema_content=canvas),
            principal=principal,
            session=session,
        )
        await et.publish_email_template_version(
            uid=created.uid, vid=v.uid, principal=principal, session=session
        )

    context = email_preview.sample_context(brand, code)
    async with session_factory() as session:
        sent = await render_published_email(
            session, tenant_id=int(staff["tenant_id"]), code=code, context=context
        )
    assert sent is not None
    assert sent.html.startswith("<!DOCTYPE html>")
    assert "#abcdef" in sent.html, "the canvas CSS was not what the send path inlined"
    assert "19px" in sent.html
    assert "Suitability Report from James Fletcher" in sent.html  # body merged
    assert "gjs" not in sent.html and "tagName" not in sent.html, (
        "editor state leaked into the mail"
    )

    preview = email_preview.render_preview(
        subject=subject,
        preheader=preheader,
        html=canvas["html"],  # type: ignore[arg-type]
        css=canvas["css"],  # type: ignore[arg-type]
        brand=brand,
        code=code,
    )
    assert preview.subject == sent.subject
    assert preview.preheader == sent.preheader
    assert preview.html == sent.html, (
        "the author's Preview of this canvas is not the email the send path produced from the "
        "saved version of the same canvas"
    )


@pytest.mark.asyncio
async def test_preview_merges_sample_values_and_uses_the_firm_brand(
    session_factory, staff: dict[str, str]
) -> None:
    """Preview renders the editor's CURRENT (unsaved) content against sample values, on the firm's
    own brand, inlined — and never touches a send path. The viewer's own name stands in for the
    sampled recipient, so the preview and a test send show the same person: the author looking."""
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(
                code=f"prev_{uuid.uuid4().hex[:6]}", name="Preview me", category="notification"
            ),
            principal=email_support.principal(staff),
            session=session,
        )
    async with session_factory() as session:
        out = await et.preview_email_template(
            uid=created.uid,
            body=et.PreviewEmail(
                subject="Hi {{ first_name }}",
                preheader="from {{ brand.name }}",
                html="<p class='b'>Welcome {{ first_name }}</p>",
                css=".b{color:#0f766e}",
            ),
            principal=email_support.principal(staff, ["email_templates:read"]),
            session=session,
        )
    assert out.subject == f"Hi {staff['first_name']}"
    assert out.preheader.startswith("from EFirm")  # the firm's real name from auth.tenants
    assert out.html.startswith("<!DOCTYPE html>")
    assert f"Welcome {staff['first_name']}" in out.html
    assert "Sarah" not in out.html  # the fixture name never reaches a real viewer
    assert "#0f766e" in out.html  # css inlined
    assert out.missing == []


@pytest.mark.asyncio
async def test_an_explicit_blank_description_clears_it_and_an_absent_key_keeps_it(
    session_factory, staff: dict[str, str]
) -> None:
    """The one CLEARABLE field on this endpoint, and it could not be cleared.

    `description = COALESCE(:desc, description)` reads a null as "leave it alone", so both list
    surfaces (each posting `trim() || null`) deleted a description, restored the old one and
    reported success. PRESENCE decides now: an explicitly-sent blank clears — landing as NULL, not
    an empty string, so a cleared description is one value rather than two for every reader to
    normalise — while an ABSENT key preserves, which is what the neighbouring `category` omission
    relies on. Both rules have to hold at the same time."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(
                code=f"invite_{uuid.uuid4().hex[:6]}",
                name="Invitation",
                description="Carries the set-password link",
            ),
            principal=principal,
            session=session,
        )
    uid = created.uid
    assert created.description == "Carries the set-password link"

    async def _update(**fields: object):
        async with session_factory() as session:
            return await et.update_email_template(
                uid=uid,
                body=et.UpdateEmailTemplate(**fields),  # type: ignore[arg-type]
                principal=principal,
                session=session,
            )

    renamed = await _update(name="Client invitation")  # says nothing about the description
    assert renamed.name == "Client invitation"
    assert renamed.description == "Carries the set-password link"

    cleared = await _update(description="")
    assert cleared.description is None
    assert cleared.name == "Client invitation"  # nothing else moved

    for blank in (None, "   "):
        await _update(description="Back again")
        assert (await _update(description=blank)).description is None


# ── the version-number race → 409, not 500 (save_version's new-draft branch + restore_version) ──
# The race needs true concurrency, so what is pinned is the translation: a UNIQUE breach → 409.


def test_a_version_number_conflict_is_a_409_not_a_500() -> None:
    with pytest.raises(HTTPException) as ei:
        et._raise_http(svc.VersionConflict("eml_x"))
    assert ei.value.status_code == 409


def test_only_a_unique_violation_is_read_as_a_version_conflict() -> None:
    def exc(code: str | None) -> object:
        return types.SimpleNamespace(orig=types.SimpleNamespace(sqlstate=code))

    assert svc._is_unique_violation(exc("23505")) is True  # unique_violation
    assert svc._is_unique_violation(exc("23001")) is False  # the immutability trigger, not this
    assert svc._is_unique_violation(exc(None)) is False


@pytest.mark.asyncio
async def test_the_subjects_asked_at_creation_survive_the_first_save(
    session_factory, staff: dict[str, str]
) -> None:
    """What an email is ABOUT is asked at creation, because it decides which event details the
    editor may list. The save REPLACES the schema, so the claim is re-posted with the body — this
    is the test for that trap: without it, a claim set at creation is lost the first time anyone
    edits the email.
    """
    principal = email_support.principal(staff)
    code = f"valuation_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(
                code=code, name="Valuation summary", subjects=["account", "contact"]
            ),
            principal=principal,
            session=session,
        )
        versions = await et.list_email_template_versions(
            uid=created.uid, principal=principal, session=session
        )
        head = await et.get_email_template_version(
            uid=created.uid, vid=versions[0].uid, principal=principal, session=session
        )
    # Canonical order, whatever order the client sent them in.
    assert head.schema_content["subjects"] == ["contact", "account"]

    async with session_factory() as session:
        saved = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(
                subject="Your valuation",
                schema={
                    "html": "<p>x</p>",
                    "css": "",
                    "source": "<p>x</p>",
                    "subjects": ["account"],
                },
            ),
            principal=principal,
            session=session,
        )
    assert saved.schema_content["subjects"] == ["account"]

    # And an emptied claim leaves NO key, so "about nothing in particular" has one spelling.
    async with session_factory() as session:
        cleared = await et.save_email_template_version(
            uid=created.uid,
            body=et.SaveEmailVersion(
                subject="Your valuation",
                schema={"html": "<p>x</p>", "css": "", "source": "<p>x</p>", "subjects": []},
            ),
            principal=principal,
            session=session,
        )
    assert "subjects" not in cleared.schema_content


@pytest.mark.asyncio
async def test_a_claim_only_draft_is_not_treated_as_worked_on(
    session_factory, staff: dict[str, str]
) -> None:
    """The head-draft staleness proxy asks whether a draft holds WORK, and a claim is not work.

    `create_template` writes the claim into v1's schema, so `schema <> '{}'` counted a virgin draft
    as worked-on: the author's first body save 30+ minutes later rolled v2 and stranded a v1 holding
    nothing but the claim. Subtracting the key is what keeps "created with a subject ticked" and
    "created with none" the same first-save behaviour.
    """
    from sqlalchemy import text

    principal = email_support.principal(staff)
    code = f"claimonly_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=code, name="Claim only", subjects=["account"]),
            principal=principal,
            session=session,
        )
        # Age the draft past the window, which is what makes the proxy decide roll-vs-in-place.
        await session.execute(
            text(
                "UPDATE email.template_versions SET created_at = now() - interval '2 hours' "
                "WHERE tenant_id = (SELECT tenant_id FROM email.templates WHERE uid = :u) "
                "  AND template_id = (SELECT id FROM email.templates WHERE uid = :u)"
            ),
            {"u": created.uid},
        )
        await session.commit()

    async with session_factory() as session:
        saved = await svc.save_version(
            session,
            int(staff["tenant_id"]),
            staff["user_uuid"],
            created.uid,
            subject="First real subject",
            preheader="",
            schema={"html": "<p>body</p>", "css": "", "source": "<p>body</p>"},
            scope="firm",
        )
        await session.commit()

    # STILL v1: the first body save landed in the draft that was already there.
    assert saved["version_number"] == 1
