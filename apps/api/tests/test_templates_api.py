"""Endpoint-behaviour tests for the /templates router (draft/publish/version lifecycle).

Drives the router coroutines against a real `authenticated` session (role + claims like
db.py::tenant_session), so the SQL, the version state machine and the pydantic shaping are all
exercised — not just the RLS predicates.
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
from app.routers import templates as tpl


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
                {
                    "uid": f"cten_{s}",
                    "alias": f"cfirm-{s}",
                    "name": f"CFirm {s}",
                },
            ).scalar_one()
        )
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Cara', 'Staff', true) RETURNING id"
                ),
                {"uid": f"cusr_{s}", "email": f"staff_{s}@c.test", "wos": f"cwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )
    return {"tenant_id": str(tenant), "user_uuid": user, "org": f"corg_{s}", "wos": f"cwos_{s}"}


def _principal(staff: dict[str, str], perms: list[str]) -> Principal:
    return Principal(
        user_id=staff["wos"], organisation_id=staff["org"], role="adviser", permissions=perms
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


PAGE = {"pages": [{"uid": "p1", "blocks": [{"uid": "b1", "type": "heading", "text": "Hi"}]}]}


@pytest.mark.asyncio
async def test_template_version_lifecycle(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["templates:write"])
    code = f"illus_{uuid.uuid4().hex[:6]}"

    # create → v1 draft, count 1, creator projected.
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=code, name="SIPP Illustration", document_type="illustration"
            ),
            principal=principal,
            session=session,
        )
    assert created.version_count == 1
    assert created.current_version_number == 1
    assert created.current_version_status == "draft"
    assert created.created_by == "Cara Staff"
    uid = created.uid

    # list + get.
    async with session_factory() as session:
        listed = await tpl.list_templates(principal=principal, session=session)
        got = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert uid in {t.uid for t in listed}
    assert got.code == code

    # save draft in place (autosave) — still v1, schema round-trips, no new version.
    async with session_factory() as session:
        v = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
    assert v.version_number == 1
    assert v.status == "draft"
    assert v.schema_content == PAGE
    vid = v.uid
    async with session_factory() as session:
        again = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert again.version_count == 1  # in-place, not a new version

    # publish v1 → published; template's current status flips.
    async with session_factory() as session:
        published = await tpl.publish_template_version(
            uid=uid, vid=vid, principal=principal, session=session
        )
        after = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert published.status == "published"
    assert after.current_version_status == "published"

    # editing a published template starts a fresh draft v2.
    async with session_factory() as session:
        v2 = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
        after2 = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert v2.version_number == 2
    assert v2.status == "draft"
    assert after2.version_count == 2
    assert after2.current_version_number == 2

    # linking datasets + an input form persists via PATCH.
    async with session_factory() as session:
        updated = await tpl.update_template(
            uid=uid,
            body=tpl.UpdateTemplate(
                linked_datasets=["projection_assumptions"], input_form_code="ff"
            ),
            principal=principal,
            session=session,
        )
    assert updated.linked_datasets == ["projection_assumptions"]
    assert updated.input_form_code == "ff"

    # archive → drops off the list.
    async with session_factory() as session:
        archived = await tpl.delete_template(uid=uid, principal=principal, session=session)
        listed_after = await tpl.list_templates(principal=principal, session=session)
    assert archived.archived_at is not None
    assert uid not in {t.uid for t in listed_after}


@pytest.mark.asyncio
async def test_stale_draft_rolls_to_new_version(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    """A non-empty draft older than the 30-minute snapshot bucket rolls forward to a fresh draft on
    the next save (freezing the previous one as an automatic restore point) instead of updating in
    place. A fresh/empty or recent draft still updates in place — covered by the lifecycle test."""
    principal = _principal(staff, ["templates:write"])
    code = f"snap_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(code=code, name="Snap", document_type="report"),
            principal=principal,
            session=session,
        )
    uid = created.uid

    # Give the head draft content — still v1 (in place).
    async with session_factory() as session:
        v1 = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
    assert v1.version_number == 1

    # Backdate the head draft past the 30-minute bucket (privileged: bypasses RLS + any clock).
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE documents.template_versions "
                "SET created_at = now() - interval '31 minutes' WHERE uid = :u"
            ),
            {"u": v1.uid},
        )
        c.commit()

    # Next save rolls forward: a new draft v2; the old v1 is frozen (version_count 1 → 2).
    async with session_factory() as session:
        v2 = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
        after = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert v2.version_number == 2
    assert v2.status == "draft"
    assert after.version_count == 2
    assert after.current_version_number == 2


@pytest.mark.asyncio
async def test_publish_requires_content(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"empty_{uuid.uuid4().hex[:6]}", name="Empty", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        versions = await tpl.list_template_versions(
            uid=created.uid, principal=principal, session=session
        )
        with pytest.raises(HTTPException) as err:
            await tpl.publish_template_version(
                uid=created.uid, vid=versions[0].uid, principal=principal, session=session
            )
    assert err.value.status_code == 422


# The blob the GrapesJS editor actually saves: content is the frame's root component's children.
GJS_BLOB = {
    "gjs_project": {
        "pages": [{"frames": [{"component": {"type": "wrapper", "components": [{"type": "t"}]}}]}]
    },
    "layout": {},
    "schema": {},
    "linkedDatasets": [],
}


@pytest.mark.asyncio
async def test_publish_accepts_editor_blob(session_factory, staff: dict[str, str]) -> None:
    """The publish content-check must recognise the real GrapesJS persist blob (not just the legacy
    pages/blocks shape) — otherwise 'add at least one block' always fires."""
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"gjs_{uuid.uuid4().hex[:6]}", name="GJS", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        saved = await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(schema_content=GJS_BLOB),
            principal=principal,
            session=session,
        )
        published = await tpl.publish_template_version(
            uid=created.uid, vid=saved.uid, principal=principal, session=session
        )
    assert published.status == "published"
    assert published.unknown_variables == []  # clean content ⇒ no false-positive warnings


@pytest.mark.asyncio
async def test_publish_warns_on_unknown_variables(session_factory, staff: dict[str, str]) -> None:
    """Publishing a version whose content references a token that isn't in the catalogue succeeds
    (soft guard — not blocked) but returns it in ``unknown_variables`` with a 'did you mean' hint.
    Real catalogue roots (``client``) are not flagged."""
    principal = _principal(staff, ["templates:write"])
    # gjs_project satisfies the content check; html carries the tokens the lint reads.
    schema = {
        **GJS_BLOB,
        "html": "<p>{{ client.name }} — {{ clientt.name }}</p>",
        "css": "",
        "components": [],
    }
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"warn_{uuid.uuid4().hex[:6]}", name="Warn", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        saved = await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(schema_content=schema),
            principal=principal,
            session=session,
        )
        published = await tpl.publish_template_version(
            uid=created.uid, vid=saved.uid, principal=principal, session=session
        )
    assert published.status == "published"  # not blocked
    by_name = {u.name: u.suggestion for u in published.unknown_variables}
    assert "clientt" in by_name  # the typo is surfaced
    assert "client" not in by_name  # a real catalogue root is not
    assert by_name["clientt"] == "client"  # suggests the intended root


@pytest.mark.asyncio
async def test_preview_and_publish_lint_identical_catalogue(
    session_factory, staff: dict[str, str]
) -> None:
    """The headline invariant: the live preview and the publish warning lint against the SAME
    catalogue. Also pins that a filtered {% for %} loop source is never flagged (structural, not a
    merge token) while a hand-typed token is."""
    principal = _principal(staff, ["templates:write", "templates:read"])
    html = (
        "<p>{{ client.name }} — {{ investor_naem }}</p>"
        "{% for h in holdings if h.value %}<span>{{ h.name }}</span>{% endfor %}"
    )
    schema = {**GJS_BLOB, "html": html, "css": "", "components": []}
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"xpath_{uuid.uuid4().hex[:6]}", name="XPath", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        saved = await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(schema_content=schema),
            principal=principal,
            session=session,
        )
        published = await tpl.publish_template_version(
            uid=created.uid, vid=saved.uid, principal=principal, session=session
        )
        preview = await tpl.render_template_preview(
            uid=created.uid,
            body=tpl.TemplatePreviewIn.model_validate({"schema": schema}),
            principal=principal,
            session=session,
        )
    pub = {u.name for u in published.unknown_variables}
    prev = {u.name for u in preview.unknown_variables}
    assert pub == prev  # same catalogue on both paths
    assert "investor_naem" in pub  # hand-typed token flagged
    assert "holdings" not in pub  # filtered-loop source never flagged
    assert "client" not in pub  # core root never flagged


def test_known_variables_includes_dotted_roots() -> None:
    """A dotted field_key / binding code resolves at render (dotted keys are nested), so the lint
    must treat its leading segment as known — otherwise it's a false 'won't fill' warning."""
    known = tpl._known_variables(
        [{"field_key": "pension.value", "field_type": "text", "label": "Pension", "options": None}],
        ["portfolio.holdings", "holdings"],
    )
    assert "pension" in known  # dotted form field_key → root known
    assert "portfolio" in known  # dotted component-binding code → root known
    assert "holdings" in known  # bare code stays known
    assert "datasets" in known


def test_has_content_shapes() -> None:
    """Unit-cover the dual-shape content check directly."""
    assert tpl._has_content(GJS_BLOB) is True
    assert tpl._has_content(PAGE) is True  # legacy pages/blocks
    assert tpl._has_content({"gjs_project": {"pages": [{"frames": [{"component": {}}]}]}}) is False
    assert tpl._has_content({}) is False


@pytest.mark.asyncio
async def test_duplicate_code_conflicts(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["templates:write"])
    code = f"dupe_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        await tpl.create_template(
            body=tpl.CreateTemplate(code=code, name="One", document_type="review"),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await tpl.create_template(
                body=tpl.CreateTemplate(code=code, name="Two", document_type="review"),
                principal=principal,
                session=session,
            )
    assert err.value.status_code == 409


@pytest.mark.asyncio
async def test_read_permission_required(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, [])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await tpl.list_templates(principal=principal, session=session)
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_preview_returns_document(session_factory, staff: dict[str, str]) -> None:
    """Preview wraps the latest version's HTML/CSS into a viewable doc, tokens left intact."""
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"prev_{uuid.uuid4().hex[:6]}", name="Prev", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(
                schema_content={
                    "html": "<div class='page'><h1>{{ title }}</h1></div>",
                    "css": ".page{color:#111}",
                    "pageSize": {"width": "210mm", "height": "297mm"},
                }
            ),
            principal=principal,
            session=session,
        )
        preview = await tpl.preview_template(uid=created.uid, principal=principal, session=session)
    assert preview.html is not None
    assert "{{ title }}" in preview.html  # tokens left intact (layout preview)
    assert "@page" in preview.html  # base + page CSS applied
    assert "<!DOCTYPE html>" in preview.html
    # On-screen preview separates stacked pages with a gap + shadow (not applied to the PDF).
    assert "margin: 0 auto 32px" in preview.html


@pytest.mark.asyncio
async def test_preview_none_without_content(session_factory, staff: dict[str, str]) -> None:
    """A fresh template (empty v1 draft) has nothing to preview."""
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"prevx_{uuid.uuid4().hex[:6]}", name="PrevX", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        preview = await tpl.preview_template(uid=created.uid, principal=principal, session=session)
    assert preview.html is None


@pytest.mark.asyncio
async def test_update_renderer_applies_to_latest_version(session_factory, staff) -> None:
    """PATCH renderer updates the latest version's renderer (it's a version property)."""
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"rend_{uuid.uuid4().hex[:6]}", name="Rend", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        v = await tpl.list_template_versions(uid=created.uid, principal=principal, session=session)
        assert v[0].renderer == "weasy"  # default
        await tpl.update_template(
            uid=created.uid,
            body=tpl.UpdateTemplate(renderer="chromium"),
            principal=principal,
            session=session,
        )
        v2 = await tpl.list_template_versions(uid=created.uid, principal=principal, session=session)
    assert v2[0].renderer == "chromium"


@pytest.mark.asyncio
async def test_template_variables_from_published(session_factory, staff) -> None:
    """The variables endpoint returns the published version's top-level Jinja variables."""
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"vars_{uuid.uuid4().hex[:6]}", name="Vars", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        # Unpublished → no variables yet.
        before = await tpl.template_variables(uid=created.uid, principal=principal, session=session)
        assert before.variables == []
        v = await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(
                schema_content={
                    # gjs_project carries the content _has_content checks; html carries the tokens.
                    "gjs_project": GJS_BLOB["gjs_project"],
                    "html": "<div class='page'>{{ client.name }}: {{ amount | currency }}</div>",
                    "css": "",
                    "pageSize": {"width": "210mm", "height": "297mm"},
                }
            ),
            principal=principal,
            session=session,
        )
        await tpl.publish_template_version(
            uid=created.uid, vid=v.uid, principal=principal, session=session
        )
        after = await tpl.template_variables(uid=created.uid, principal=principal, session=session)
    assert after.variables == ["amount", "client"]  # sorted top-level names


@pytest.mark.asyncio
async def test_archive_view_and_restore(session_factory, staff: dict[str, str]) -> None:
    """Archive drops a template off the live list but keeps it in the archived slice; unarchive
    restores it — the reversible archive/view flow that mirrors the documents library."""
    principal = _principal(staff, ["templates:write", "templates:read"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"arch_{uuid.uuid4().hex[:6]}", name="Archivable", document_type="review"
            ),
            principal=principal,
            session=session,
        )
    uid = created.uid

    # Archive → off the live list, present in the archived slice (and in `all`).
    async with session_factory() as session:
        await tpl.delete_template(uid=uid, principal=principal, session=session)
        live = await tpl.list_templates(status="live", principal=principal, session=session)
        archived = await tpl.list_templates(status="archived", principal=principal, session=session)
        both = await tpl.list_templates(status="all", principal=principal, session=session)
    assert uid not in {t.uid for t in live}
    assert uid in {t.uid for t in archived}
    assert uid in {t.uid for t in both}

    # Restore → back on the live list, off the archived slice, archived_at cleared.
    async with session_factory() as session:
        restored = await tpl.unarchive_template(uid=uid, principal=principal, session=session)
        live2 = await tpl.list_templates(status="live", principal=principal, session=session)
        arch2 = await tpl.list_templates(status="archived", principal=principal, session=session)
    assert restored.archived_at is None
    assert uid in {t.uid for t in live2}
    assert uid not in {t.uid for t in arch2}


@pytest.mark.asyncio
async def test_unarchive_unknown_is_404(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await tpl.unarchive_template(uid="tpl_missing", principal=principal, session=session)
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_template_clones_into_a_fresh_draft(
    session_factory, staff: dict[str, str]
) -> None:
    """Duplicate copies metadata + the latest version's content into a NEW template — name suffixed
    "(copy)", code de-duplicated, always a draft (even from a published source), source intact."""
    principal = _principal(staff, ["templates:write", "templates:read"])
    code = f"dup_{uuid.uuid4().hex[:6]}"
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(code=code, name="Original", document_type="report"),
            principal=principal,
            session=session,
        )
    uid = created.uid

    # Give it content + metadata, then publish — so the source's latest version is 'published'.
    async with session_factory() as session:
        v = await tpl.save_template_version(
            uid=uid, body=tpl.SaveVersion(schema_content=PAGE), principal=principal, session=session
        )
        await tpl.update_template(
            uid=uid,
            body=tpl.UpdateTemplate(linked_datasets=["assumptions"], input_form_code="ff1"),
            principal=principal,
            session=session,
        )
        await tpl.publish_template_version(uid=uid, vid=v.uid, principal=principal, session=session)

    # Duplicate → new template, metadata copied, name/code derived, a fresh draft (not published).
    async with session_factory() as session:
        copy = await tpl.duplicate_template(uid=uid, principal=principal, session=session)
    assert copy.uid != uid
    assert copy.name == "Original (copy)"
    assert copy.code == f"{code}_copy"
    assert copy.document_type == "report"
    assert copy.linked_datasets == ["assumptions"]
    assert copy.input_form_code == "ff1"
    assert copy.current_version_status == "draft"
    assert copy.current_version_number == 1
    assert copy.version_count == 1

    # The cloned version carries the source's content; the source is untouched.
    async with session_factory() as session:
        vid = (
            await session.execute(
                text(
                    "SELECT v.uid FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id WHERE t.uid = :u"
                ),
                {"u": copy.uid},
            )
        ).scalar_one()
        detail = await tpl.get_template_version(
            uid=copy.uid, vid=vid, principal=principal, session=session
        )
        src = await tpl.get_template(uid=uid, principal=principal, session=session)
    assert detail.schema_content == PAGE
    assert src.current_version_status == "published"

    # A second duplicate of the same source de-duplicates the code again.
    async with session_factory() as session:
        copy2 = await tpl.duplicate_template(uid=uid, principal=principal, session=session)
    assert copy2.code == f"{code}_copy_2"


@pytest.mark.asyncio
async def test_duplicate_unknown_is_404(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(staff, ["templates:write"])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as err:
            await tpl.duplicate_template(uid="tpl_missing", principal=principal, session=session)
    assert err.value.status_code == 404


async def _fake_render_pdf(
    html: str,
    css: str = "",
    branding_css: str = "",
    page_size: dict[str, str] | None = None,
    renderer: str | None = None,
) -> tuple[bytes, str]:
    """Stand-in for worker render delegation: asserts HTML is token-filled, returns a fake PDF."""
    assert "{{ client.name }}" not in html  # token sample-filled before render
    assert "<h1>" in html  # structure rendered
    return b"%PDF-1.4 fake", "sha"


@pytest.mark.asyncio
async def test_preview_pdf_renders_via_the_real_engine(
    session_factory, staff: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The POST `.pdf` preview renders the sample-filled schema through the same path as generation
    (``render_pdf``, mocked here) and returns a PDF; the HTML it gets is already token-filled."""
    monkeypatch.setattr(tpl, "render_pdf", _fake_render_pdf)
    principal = _principal(staff, ["templates:write", "templates:read"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"pdfprev_{uuid.uuid4().hex[:6]}", name="PDFPrev", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        resp = await tpl.render_template_preview_pdf(
            uid=created.uid,
            body=tpl.TemplatePreviewIn.model_validate(
                {
                    "schema": {
                        "html": "<div class='page'><h1>{{ client.name }}</h1></div>",
                        "css": "",
                        "pageSize": {"width": "210mm", "height": "297mm"},
                    }
                }
            ),
            principal=principal,
            session=session,
        )
    assert resp.media_type == "application/pdf"
    assert resp.body == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_preview_pdf_get_renders_latest_saved_version(
    session_factory, staff: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GET `.pdf` preview (the overview's) renders the latest SAVED version's schema through the
    same delegation, so the overview shows the true paginated document."""
    monkeypatch.setattr(tpl, "render_pdf", _fake_render_pdf)
    principal = _principal(staff, ["templates:write", "templates:read"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"pdfget_{uuid.uuid4().hex[:6]}", name="GetPrev", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        # Save content so the latest version has something to render.
        await tpl.save_template_version(
            uid=created.uid,
            body=tpl.SaveVersion(
                schema_content={"html": "<div class='page'><h1>{{ client.name }}</h1></div>"}
            ),
            principal=principal,
            session=session,
        )
        resp = await tpl.preview_template_pdf(uid=created.uid, principal=principal, session=session)
    assert resp.media_type == "application/pdf"
    assert resp.body == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_preview_pdf_get_422_without_content(session_factory, staff: dict[str, str]) -> None:
    """The overview's GET preview on an empty template is a clear 422 (nothing to render)."""
    principal = _principal(staff, ["templates:write", "templates:read"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"getempty_{uuid.uuid4().hex[:6]}", name="GetEmpty", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        with pytest.raises(HTTPException) as err:
            await tpl.preview_template_pdf(uid=created.uid, principal=principal, session=session)
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_pdf_422_without_content(session_factory, staff: dict[str, str]) -> None:
    """A template with no content has nothing to render — a clear 422, not an empty/blank PDF."""
    principal = _principal(staff, ["templates:write", "templates:read"])
    async with session_factory() as session:
        created = await tpl.create_template(
            body=tpl.CreateTemplate(
                code=f"pdfempty_{uuid.uuid4().hex[:6]}", name="Empty", document_type="review"
            ),
            principal=principal,
            session=session,
        )
        with pytest.raises(HTTPException) as err:
            await tpl.render_template_preview_pdf(
                uid=created.uid,
                body=tpl.TemplatePreviewIn.model_validate({"schema": {}}),
                principal=principal,
                session=session,
            )
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_the_illustration_layout_is_a_document_template(
    session_factory, staff: dict[str, str]
) -> None:
    """Opening the library publishes the GIA/ISA layout once. A second open does not add another."""
    principal = _principal(staff, ["templates:read"])
    async with session_factory() as session:
        first = await tpl.list_templates(principal=principal, session=session)
        illustration = [row for row in first if row.code == "gia_isa_illustration"]
        assert len(illustration) == 1
        assert illustration[0].name == "GIA and ISA illustration"
        assert illustration[0].document_type == "illustration"
        assert illustration[0].current_version_status == "published"
        html = (
            await session.execute(
                text(
                    "SELECT v.schema ->> 'html' FROM documents.templates t "
                    "JOIN documents.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.code = 'gia_isa_illustration'"
                )
            )
        ).scalar_one()
        assert "Projected value" in html
        versions = (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.code = 'gia_isa_illustration'"
                )
            )
        ).scalar_one()
        second = await tpl.list_templates(principal=principal, session=session)
        assert [row.uid for row in second if row.code == "gia_isa_illustration"] == [
            illustration[0].uid
        ]
        again = (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.code = 'gia_isa_illustration'"
                )
            )
        ).scalar_one()
        assert again == versions
