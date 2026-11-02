"""Templates — the document-template authoring surface (persistence for the platform editor).

Firm staff author versioned document templates in the Svelte editor (docs/document-generation.md).
This lands the persistence those definitions were drawn to mirror: a template record
(``documents.templates``) plus its version history (``documents.template_versions``), both
tenant-scoped. Runs under ``tenant_session`` (RLS ``authenticated`` enforces tenant isolation); each
action is gated by a ``templates:*`` permission.

Authoring only. The async render pipeline (PGMQ worker + WeasyPrint + ``POST /documents`` generate),
the ``preview``/``diff`` review endpoints, and the datasets API are separate later slices. A version

``schema`` is the editor's ``TemplateContent`` (pages→blocks). Publish validation here is
"has content", plus a **soft, non-blocking** unknown-variable warning: both the live preview and
publish lint the content's merge tokens against the template's catalogue (core context + linked
form fields + datasets) and surface any that won't fill. Full Jinja-syntax / render validation
still lands with generation.
"""

import difflib
import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, require_permission
from app.db import target_tenant_session as tenant_session
from app.document_types import register_document_type
from app.ids import new_id
from app.permissions import TEMPLATES_READ, TEMPLATES_WRITE
from app.render import jinja_engine
from app.render.components import dataset_codes, loop_sources, substitute_components
from app.render.illustration_template_store import ensure_illustration_template
from app.render.renderers import preview_html_document, render_pdf
from app.render.resolve import first_rows
from app.render.sample import sample_context, sample_datasets
from app.routers.forms import FormFieldOut

router = APIRouter(prefix="/templates", tags=["templates"])

Renderer = Literal["weasy", "chromium"]

# A template + its version count + latest-version (number/status) + creator name.
_SELECT = (
    "SELECT t.uid, t.code, t.name, t.document_type, t.description, t.linked_datasets, "
    "       t.input_form_code, t.archived_at, t.created_at, u.name AS created_by, "
    "       (SELECT count(*) FROM documents.template_versions v WHERE v.template_id = t.id) "
    "         AS version_count, "
    "       lv.version_number AS current_version_number, "
    "       lv.status::text AS current_version_status "
    "FROM documents.templates t "
    "LEFT JOIN auth.users u ON u.id = t.created_by "
    "LEFT JOIN LATERAL ("
    "  SELECT version_number, status FROM documents.template_versions v "
    "  WHERE v.template_id = t.id ORDER BY version_number DESC LIMIT 1"
    ") lv ON true "
)

_VERSION_COLS = (
    "v.uid, v.version_number, v.status::text AS status, v.renderer::text AS renderer, "
    "v.created_at, cu.name AS created_by, v.published_at, pu.name AS published_by"
)
_VERSION_FROM = (
    "FROM documents.template_versions v "
    "LEFT JOIN auth.users cu ON cu.id = v.created_by "
    "LEFT JOIN auth.users pu ON pu.id = v.published_by "
)


class TemplateOut(BaseModel):
    uid: str
    code: str
    name: str
    document_type: str
    description: str | None
    linked_datasets: list[str]
    input_form_code: str | None
    version_count: int
    current_version_number: int | None
    current_version_status: str | None
    archived_at: datetime | None
    created_at: datetime
    created_by: str | None


class VersionOut(BaseModel):
    uid: str
    version_number: int
    status: str
    renderer: str
    created_at: datetime
    created_by: str | None
    published_at: datetime | None
    published_by: str | None


class VersionDetailOut(VersionOut):
    # `schema` is the editor's TemplateContent. Field is `schema_content` internally to avoid any
    # ambiguity with pydantic internals; the wire name is `schema` via the alias.
    schema_content: dict[str, Any] = Field(default_factory=dict, alias="schema")
    # The linked input form frozen at publish (code/name/fields); null for drafts + pre-snapshot
    # versions. Generation reads this so a published template is immune to later form edits.
    input_form_snapshot: dict[str, Any] | None = None
    model_config = {"populate_by_name": True}


class InputFormOut(BaseModel):
    """The fields to ask when generating from a template. `source` says where they came from:
    'snapshot' (the published version's frozen form — authoritative), 'live' (a pre-snapshot
    published version falling back to the current form by code), or 'none'."""

    fields: list[FormFieldOut]
    source: Literal["snapshot", "live", "none"]


class CreateTemplate(BaseModel):
    code: str
    name: str
    document_type: str
    renderer: Renderer = "weasy"
    description: str | None = None
    linked_datasets: list[str] = []
    input_form_code: str | None = None


class UpdateTemplate(BaseModel):
    name: str | None = None
    code: str | None = None
    document_type: str | None = None
    description: str | None = None
    linked_datasets: list[str] | None = None
    input_form_code: str | None = None
    # Renderer lives on the version (not the template); when set, it applies to the latest version.
    renderer: Renderer | None = None


class SaveVersion(BaseModel):
    schema_content: dict[str, Any] = Field(alias="schema")
    renderer: Renderer | None = None
    model_config = {"populate_by_name": True}


async def _fetch(session: AsyncSession, uid: str) -> TemplateOut | None:
    row = (
        (await session.execute(text(_SELECT + "WHERE t.uid = :uid"), {"uid": uid}))
        .mappings()
        .one_or_none()
    )
    return TemplateOut(**row) if row else None


async def _template_id(session: AsyncSession, uid: str) -> int | None:
    """Internal id for a template uid, RLS-scoped (None if not visible/absent)."""
    return (
        await session.execute(
            text("SELECT id FROM documents.templates WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()


def _version_detail(row: Any) -> VersionDetailOut:
    # The SQL aliases the jsonb column to `schema_content` (the field name); populate by name.
    data = dict(row)
    data["schema_content"] = data.get("schema_content") or {}
    return VersionDetailOut(**data)


@router.get("", name="list-templates")
async def list_templates(
    # Searched in SQL rather than in the browser: the page filters `name` and `code`, and
    # doing that here is what makes its question reach past the rows already sent.
    q: str | None = None,
    status: Literal["live", "archived", "all"] = "live",
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[TemplateOut]:
    """The firm's templates (RLS-scoped), newest first. ``status`` selects the slice — ``live``
    (default; archived excluded), ``archived`` (only archived), or ``all`` — mirroring how the
    documents library exposes its archived view."""
    require_permission(principal, TEMPLATES_READ)
    # The GIA/ISA illustration layout is code-owned until the firm has a copy. Publishing it
    # here is what puts it on this page; a firm that has already edited it is left alone.
    await ensure_illustration_template(session)
    where = {
        "live": "WHERE t.archived_at IS NULL ",
        "archived": "WHERE t.archived_at IS NOT NULL ",
        "all": "",
    }[status]
    params: dict[str, str] = {}
    if q and q.strip():
        # Appended to whichever slice `status` chose, so a search never widens past it.
        where += ("AND " if where else "WHERE ") + "(t.name ILIKE :q OR t.code ILIKE :q) "
        params["q"] = f"%{q.strip()}%"
    rows = (
        (
            await session.execute(
                # `where` is fixed text plus bound parameters — no caller string reaches the SQL.
                text(_SELECT + where + "ORDER BY t.created_at DESC, t.id DESC"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )
    return [TemplateOut(**r) for r in rows]


@router.post("", name="create-template", status_code=status.HTTP_201_CREATED)
async def create_template(
    body: CreateTemplate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """Create a template with an initial draft version (v1, empty). ``code`` unique per firm."""
    require_permission(principal, TEMPLATES_WRITE)
    # Open-ended type: register the template's type as a reusable suggestion (returns the slug).
    dtype = await register_document_type(session, body.document_type)
    template_uid = new_id()
    try:
        template_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, description, linked_datasets, "
                    "   input_form_code, created_by) "
                    "VALUES (:uid, auth.tenant_id(), :code, :name, :dtype, :desc, :links, "
                    "  :form, auth.uid()) RETURNING id"
                ),
                {
                    "uid": template_uid,
                    "code": body.code,
                    "name": body.name,
                    "dtype": dtype,
                    "desc": body.description,
                    "links": body.linked_datasets,
                    "form": body.input_form_code,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with this code already exists.",
        ) from exc
    await session.execute(
        text(
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, renderer, schema, created_by) "
            "VALUES (:uid, auth.tenant_id(), :tid, 1, 'draft', :renderer, '{}'::jsonb, auth.uid())"
        ),
        {"uid": new_id(), "tid": template_id, "renderer": body.renderer},
    )
    template = await _fetch(session, template_uid)
    if template is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Create failed"
        )
    return template


@router.get("/{uid}", name="get-template")
async def get_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """One template's metadata (RLS-scoped)."""
    require_permission(principal, TEMPLATES_READ)
    template = await _fetch(session, uid)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


@router.patch("/{uid}", name="update-template")
async def update_template(
    uid: str,
    body: UpdateTemplate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """Update a template's metadata, its linked datasets, and/or its input-form link."""
    require_permission(principal, TEMPLATES_WRITE)
    try:
        row = (
            await session.execute(
                text(
                    "UPDATE documents.templates SET "
                    "  name = COALESCE(:name, name), "
                    "  code = COALESCE(:code, code), "
                    "  document_type = COALESCE(:dtype, document_type), "
                    "  description = COALESCE(:desc, description), "
                    "  linked_datasets = COALESCE(:links, linked_datasets), "
                    "  input_form_code = CASE WHEN :form_set THEN :form ELSE input_form_code END "
                    "WHERE uid = :uid RETURNING id"
                ),
                {
                    "name": body.name,
                    "code": body.code,
                    "dtype": body.document_type,
                    "desc": body.description,
                    "links": body.linked_datasets,
                    # input_form_code is nullable + clearable, so distinguish "unset" from "→ null".
                    "form_set": "input_form_code" in body.model_fields_set,
                    "form": body.input_form_code,
                    "uid": uid,
                },
            )
        ).first()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with this code already exists.",
        ) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    # Renderer is a version property; apply it to the latest version (what the editor works on).
    if body.renderer is not None:
        await session.execute(
            text(
                "UPDATE documents.template_versions SET renderer = CAST(:r AS documents.renderer) "
                "WHERE template_id = :tid AND version_number = "
                "  (SELECT max(version_number) FROM documents.template_versions "
                "     WHERE template_id = :tid)"
            ),
            {"r": body.renderer, "tid": row[0]},
        )
    template = await _fetch(session, uid)
    if template is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed"
        )
    return template


@router.delete("/{uid}", name="delete-template")
async def delete_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """Archive a template (soft — sets ``archived_at``): drops off the list, versions preserved."""
    require_permission(principal, TEMPLATES_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.templates SET archived_at = now() "
                "WHERE uid = :uid AND archived_at IS NULL RETURNING id"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None:
        # Distinguish "no such template" (404) from "already archived" (still return it).
        template = await _fetch(session, uid)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return template
    template = await _fetch(session, uid)
    if template is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Archive failed"
        )
    return template


@router.post("/{uid}/unarchive", name="unarchive-template")
async def unarchive_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """Restore an archived template to the live list (clears ``archived_at``) — reverses the
    archive (DELETE) action, the same flow the documents library uses for archived items."""
    require_permission(principal, TEMPLATES_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.templates SET archived_at = NULL "
                "WHERE uid = :uid AND archived_at IS NOT NULL RETURNING id"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None:
        # Distinguish "no such template" (404) from "already live" (return it unchanged).
        template = await _fetch(session, uid)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return template
    template = await _fetch(session, uid)
    if template is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Restore failed"
        )
    return template


def _unique_code(base: str, existing: set[str]) -> str:
    """A ``{base}_copy`` code free of collisions (suffix a counter if taken). Codes are UNIQUE per
    firm, so a duplicate needs its own."""
    candidate = f"{base}_copy"
    if candidate not in existing:
        return candidate
    i = 2
    while f"{base}_copy_{i}" in existing:
        i += 1
    return f"{base}_copy_{i}"


@router.post("/{uid}/duplicate", name="duplicate-template", status_code=status.HTTP_201_CREATED)
async def duplicate_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateOut:
    """Copy a template into a NEW draft: same metadata + content, name suffixed "(copy)" and code
    de-duplicated, a fresh v1 draft cloned from the source's latest version. A copy always starts as
    an editable draft (never published), so the editor opens on it ready to change. The source is
    untouched."""
    require_permission(principal, TEMPLATES_WRITE)
    src = (
        (
            await session.execute(
                text("SELECT id, code, name FROM documents.templates WHERE uid = :uid"),
                {"uid": uid},
            )
        )
        .mappings()
        .first()
    )
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    existing = {
        r[0] for r in (await session.execute(text("SELECT code FROM documents.templates"))).all()
    }
    copy_uid = new_id()
    try:
        # Copy the metadata (type/description/links/form) from the source; override code + name.
        copy_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, description, linked_datasets, "
                    "   input_form_code, created_by) "
                    "SELECT :copy_uid, auth.tenant_id(), :code, :name, t.document_type, "
                    "       t.description, t.linked_datasets, t.input_form_code, auth.uid() "
                    "FROM documents.templates t WHERE t.uid = :src_uid RETURNING id"
                ),
                {
                    "copy_uid": copy_uid,
                    "code": _unique_code(src["code"], existing),
                    "name": f"{src['name']} (copy)",
                    "src_uid": uid,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:  # a code collided (race) despite the de-dupe above
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not duplicate — a template with this code already exists.",
        ) from exc
    # Clone the source's latest version into a fresh v1 draft (schema + renderer, cloned in SQL).
    # Every template has a v1, so this copies exactly one row.
    cloned = await session.execute(
        text(
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, renderer, schema, created_by) "
            "SELECT :vuid, auth.tenant_id(), :tid, 1, 'draft', v.renderer, v.schema, auth.uid() "
            "FROM documents.template_versions v WHERE v.template_id = :src_id "
            "ORDER BY v.version_number DESC LIMIT 1 RETURNING id"
        ),
        {"vuid": new_id(), "tid": copy_id, "src_id": src["id"]},
    )
    if cloned.first() is None:  # source somehow had no version — give the copy an empty draft
        await session.execute(
            text(
                "INSERT INTO documents.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                "   created_by) "
                "VALUES (:vuid, auth.tenant_id(), :tid, 1, 'draft', 'weasy', '{}'::jsonb, "
                "  auth.uid())"
            ),
            {"vuid": new_id(), "tid": copy_id},
        )
    template = await _fetch(session, copy_uid)
    if template is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Duplicate failed"
        )
    return template


class UnknownVariable(BaseModel):
    # A referenced token not in the catalogue, + the closest catalogue key (a likely typo fix).
    name: str
    suggestion: str | None = None


class TemplatePreviewOut(BaseModel):
    # A complete, browser-viewable HTML document for an iframe, or None if there's nothing to
    # preview yet (no version, or the version has no saved render content).
    html: str | None
    # Variables the template references that aren't in its catalogue — likely hand-typed / mistyped
    # tokens that won't populate. Empty for the layout-only GET preview; computed for the POST one.
    unknown_variables: list[UnknownVariable] = []


async def _form_fields_for_code(session: AsyncSession, code: str | None) -> list[dict[str, Any]]:
    """The linked input form's fields (ordered) — empty when the template has no input form."""
    if not code:
        return []
    return [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT ff.field_key, ff.field_type, ff.label, ff.options "
                    "FROM documents.form_fields ff "
                    "JOIN documents.forms f ON f.id = ff.form_id "
                    "WHERE f.code = :code ORDER BY ff.sort_order, ff.id"
                ),
                {"code": code},
            )
        )
        .mappings()
        .all()
    ]


def _known_variables(form_fields: list[dict[str, Any]], codes: list[str]) -> set[str]:
    """A template's variable catalogue: the core sample-context keys (client/adviser/firm/today plus
    one per linked form field), every dataset code it draws on, and the ``datasets`` map. The lint
    checks referenced tokens against this set."""
    known = (
        set(sample_context(form_fields).keys()) | {str(c) for c in codes if c} | {"datasets", "one"}
    )
    # The lint compares against a token's top-level Jinja root, and ``render`` nests dotted keys
    # (``_expand_dotted_keys``) — so a dotted catalogue key (a dotted field_key or component
    # binding) is reachable via its leading segment. Add those roots so it isn't a false positive.
    known |= {k.split(".", 1)[0] for k in known if "." in k}
    return known


def _lint_unknowns(html: str, known: set[str]) -> list[UnknownVariable]:
    """Tokens the template references that aren't in ``known`` (hand-typed / mistyped merge tokens),
    each with the closest catalogue key as a "did you mean" hint. Shared by the live preview and the
    publish-time warning so the two never diverge."""
    ordered = sorted(known)
    return [
        UnknownVariable(
            name=name,
            suggestion=next(iter(difflib.get_close_matches(name, ordered, n=1, cutoff=0.6)), None),
        )
        for name in jinja_engine.unknown_variables(html, known)
    ]


async def unresolved_variables(
    session: AsyncSession, uid: str, schema: Any
) -> list[UnknownVariable]:
    """Template variables a saved version's schema references that have NO source — i.e. aren't in
    the catalogue (common context + attached form fields + linked datasets). Shared by the live
    preview lint, the publish-time warning, and the generate gate (documents.py) so all three agree.
    Empty when there's nothing renderable."""
    if not isinstance(schema, dict):
        return []
    html = schema.get("html")
    if not html:
        return []
    components = schema.get("components") or []
    trow = (
        (
            await session.execute(
                text(
                    "SELECT input_form_code, linked_datasets "
                    "FROM documents.templates WHERE uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if trow is None:
        return []
    form_fields = await _form_fields_for_code(session, trow["input_form_code"])
    codes = [*(trow["linked_datasets"] or []), *dataset_codes(components), *loop_sources(str(html))]
    return _lint_unknowns(str(html), _known_variables(form_fields, codes))


@router.get("/{uid}/preview", name="preview-template")
async def preview_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplatePreviewOut:
    """On-screen preview of the template's latest version (draft or published): its saved HTML/CSS
    wrapped with the same base + page CSS the PDF renderer uses, for display in an iframe. Layout
    preview — Jinja tokens are left intact; a data-filled render is the generate flow."""
    require_permission(principal, TEMPLATES_READ)
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.schema FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid ORDER BY v.version_number DESC LIMIT 1"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    schema = row["schema"] or {}
    html = schema.get("html") if isinstance(schema, dict) else None
    if not html:
        return TemplatePreviewOut(html=None)
    css = (schema.get("css") if isinstance(schema, dict) else "") or ""
    page_size = schema.get("pageSize") if isinstance(schema, dict) else None
    return TemplatePreviewOut(html=preview_html_document(str(html), css, page_size))


class TemplatePreviewIn(BaseModel):
    # The editor's CURRENT (possibly unsaved) schema — html / css / components / pageSize.
    # Aliased: `schema` is a reserved BaseModel attribute, so the field is `template_schema` but the
    # wire key stays "schema".
    template_schema: dict[str, Any] = Field(alias="schema")


async def _rendered_preview(
    session: AsyncSession, uid: str, schema: dict[str, Any]
) -> tuple[str | None, str, dict[str, str] | None, list[UnknownVariable]]:
    """Sample-fill the posted schema through the real pipeline (Jinja + chart/table
    markers) — the path the worker uses at generation. Returns the rendered HTML +
    css + page_size + unknown variables, shared by the HTML and PDF previews."""
    trow = (
        (
            await session.execute(
                text(
                    "SELECT input_form_code, linked_datasets "
                    "FROM documents.templates WHERE uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if trow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    html = schema.get("html")
    if not html:
        return None, "", None, []
    css = schema.get("css") or ""
    components = schema.get("components") or []
    page_size = schema.get("pageSize")
    # Sample values for the linked input form's fields (if any) drive the merge tokens.
    form_fields = await _form_fields_for_code(session, trow["input_form_code"])
    tenant_id = int((await session.execute(text("SELECT auth.tenant_id()"))).scalar_one())
    # Resolve every dataset the template draws on (links + chart/table bindings + Jinja loops).
    codes = [*(trow["linked_datasets"] or []), *dataset_codes(components), *loop_sources(str(html))]
    datasets = await sample_datasets(session, tenant_id, codes)
    # datasets spread FIRST so the client/adviser/firm sample context wins on any name clash — a
    # dataset code equal to a reserved root (e.g. `client`) can't shadow it (matches the worker).
    # `one` = first row per dataset (single-value bindings), built the same way the worker does.
    variables = {
        **datasets,
        **sample_context(form_fields),
        "datasets": datasets,
        "one": first_rows(datasets),
    }
    # Lint: tokens referenced but not in the catalogue (hand-typed / mistyped) — surfaced in the
    # editor so advisers catch drift before generating, each with a "did you mean" hint. Same
    # catalogue as the publish-time warning (_unknown_for_version).
    unknown = _lint_unknowns(str(html), _known_variables(form_fields, codes))
    rendered = jinja_engine.render(str(html), variables)
    # Replace <g-chart>/<g-table> markers with server-rendered SVG/HTML (the canvas can't).
    rendered = substitute_components(rendered, components, datasets)
    return rendered, css, page_size, unknown


@router.post("/{uid}/preview", name="render-template-preview")
async def render_template_preview(
    uid: str,
    body: TemplatePreviewIn,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplatePreviewOut:
    """Data-filled live preview (HTML, browser-rendered): the sample-filled schema wrapped with the
    base + page CSS for an iframe. Fast (no WeasyPrint) so the editor can call it live for layout +
    the unknown-token lint; the true-to-generation render is ``/preview.pdf`` below."""
    require_permission(principal, TEMPLATES_READ)
    rendered, css, page_size, unknown = await _rendered_preview(
        session, uid, body.template_schema or {}
    )
    if rendered is None:
        return TemplatePreviewOut(html=None)
    return TemplatePreviewOut(
        html=preview_html_document(rendered, css, page_size), unknown_variables=unknown
    )


@router.post("/{uid}/preview.pdf", name="render-template-preview-pdf")
async def render_template_preview_pdf(
    uid: str,
    body: TemplatePreviewIn,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """True-to-generation preview: render the CURRENT (posted) schema — sample-filled through the
    same code path as the HTML preview — with the SAME engine generation uses (``renderers.py``),
    and return the PDF. So pagination, page breaks, overflow and running headers/footers appear
    exactly as the generated document, which the browser HTML preview cannot (a different layout
    engine that never paginates). Uses the latest version's renderer to match what generation picks.
    Synchronous WeasyPrint (~seconds) — an explicit action, not the live layout preview.

    Requires ``templates:write``, not ``:read``: this renders CALLER-supplied, unsaved HTML on the
    server (the fetcher is hardened, but a server-side render of arbitrary markup is still an
    authoring action). Read-only staff preview the SAVED version via the GET sibling instead."""
    require_permission(principal, TEMPLATES_WRITE)
    rendered, css, page_size, _unknown = await _rendered_preview(
        session, uid, body.template_schema or {}
    )
    if rendered is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to render yet — the template has no content.",
        )
    renderer_name = (
        await session.execute(
            text(
                "SELECT v.renderer::text FROM documents.template_versions v "
                "JOIN documents.templates t ON t.id = v.template_id "
                "WHERE t.uid = :uid ORDER BY v.version_number DESC LIMIT 1"
            ),
            {"uid": uid},
        )
    ).scalar_one_or_none()
    pdf, _sha = await render_pdf(rendered, css, page_size=page_size, renderer=renderer_name)
    return Response(content=pdf, media_type="application/pdf")


@router.get("/{uid}/preview.pdf", name="preview-template-pdf")
async def preview_template_pdf(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """True-to-generation preview of the latest SAVED version — the template overview's preview.
    Sample-filled through the same pipeline as generation and rendered on the same engine (the
    worker, via ``render_pdf``), so pagination/overflow appear exactly as the generated document
    (the old HTML overview thumbnail clipped at one page). GET so the overview can lazily load it
    into an ``<iframe>`` without blocking the page render."""
    require_permission(principal, TEMPLATES_READ)
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.schema, v.renderer::text AS renderer "
                    "FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid ORDER BY v.version_number DESC LIMIT 1"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    rendered, css, page_size, _unknown = await _rendered_preview(session, uid, schema)
    if rendered is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to render yet — the template has no content.",
        )
    pdf, _sha = await render_pdf(rendered, css, page_size=page_size, renderer=row["renderer"])
    return Response(content=pdf, media_type="application/pdf")


class TemplateVariablesOut(BaseModel):
    # Top-level variable names the published version references (what generation would need).
    variables: list[str]


@router.get("/{uid}/variables", name="template-variables")
async def template_variables(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TemplateVariablesOut:
    """The variables the template's **published** version references — so the generate flow can warn
    when the collected inputs won't fill them all. (Published = what generation renders; empty
    if the template isn't published yet.)"""
    require_permission(principal, TEMPLATES_READ)
    schema = (
        await session.execute(
            text(
                "SELECT v.schema FROM documents.template_versions v "
                "JOIN documents.templates t ON t.id = v.template_id "
                "WHERE t.uid = :uid AND v.id = t.current_published_version_id"
            ),
            {"uid": uid},
        )
    ).scalar_one_or_none()
    html = schema.get("html") if isinstance(schema, dict) else None
    variables = jinja_engine.undeclared_variables(str(html)) if html else []
    return TemplateVariablesOut(variables=variables)


@router.get("/{uid}/versions", name="list-template-versions")
async def list_template_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[VersionOut]:
    """A template's version history (newest first), without the (potentially large) schema."""
    require_permission(principal, TEMPLATES_READ)
    tid = await _template_id(session, uid)
    if tid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS} {_VERSION_FROM} "
                    "WHERE v.template_id = :tid ORDER BY v.version_number DESC"
                ),
                {"tid": tid},
            )
        )
        .mappings()
        .all()
    )
    return [VersionOut(**r) for r in rows]


@router.get("/{uid}/versions/{vid}", name="get-template-version")
async def get_template_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> VersionDetailOut:
    """One version incl. its ``schema`` (the editor's content) — what the editor loads."""
    require_permission(principal, TEMPLATES_READ)
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS}, v.schema AS schema_content, "
                    f"v.input_form_snapshot {_VERSION_FROM} "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid AND v.uid = :vid"
                ),
                {"uid": uid, "vid": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return _version_detail(row)


@router.get("/{uid}/input-form", name="template-input-form")
async def template_input_form(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> InputFormOut:
    """The fields the generate dialog should ask for this template. Prefers the published version's
    frozen snapshot (so generation is immune to later form edits); falls back to the live form by
    code for versions published before snapshots existed; empty when there's no input form."""
    require_permission(principal, TEMPLATES_READ)
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.input_form_code, v.input_form_snapshot "
                    "FROM documents.templates t "
                    "LEFT JOIN documents.template_versions v "
                    "  ON v.id = t.current_published_version_id "
                    "WHERE t.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    snap = row["input_form_snapshot"]
    if isinstance(snap, dict) and isinstance(snap.get("fields"), list):
        return InputFormOut(fields=[FormFieldOut(**f) for f in snap["fields"]], source="snapshot")
    code = row["input_form_code"]
    if code:
        form_id = (
            await session.execute(
                text(
                    "SELECT id FROM documents.forms "
                    "WHERE code = :c AND tenant_id = auth.tenant_id()"
                ),
                {"c": code},
            )
        ).scalar_one_or_none()
        if form_id is not None:
            fields = await _form_fields(session, form_id)
            return InputFormOut(fields=[FormFieldOut(**f) for f in fields], source="live")
    return InputFormOut(fields=[], source="none")


async def _version_detail_by_id(session: AsyncSession, version_id: int) -> VersionDetailOut:
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS}, v.schema AS schema_content, "
                    f"v.input_form_snapshot {_VERSION_FROM} "
                    "WHERE v.id = :id"
                ),
                {"id": version_id},
            )
        )
        .mappings()
        .one()
    )
    return _version_detail(row)


# The FormFieldOut columns, for reading a form's fields (snapshot capture + the live fallback).
_FORM_FIELD_COLS = (
    "uid, field_key, field_type, label, section, description, required, "
    "conditions, validation, options, sort_order"
)


async def _form_fields(session: AsyncSession, form_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    # _FORM_FIELD_COLS is a fixed column list, not user input.
                    f"SELECT {_FORM_FIELD_COLS} FROM documents.form_fields "  # noqa: S608
                    "WHERE form_id = :fid ORDER BY sort_order, id"
                ),
                {"fid": form_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _capture_form_snapshot(session: AsyncSession, template_id: int, version_id: int) -> None:
    """Freeze the template's linked input form onto the version being published. A published
    template is then immune to later edits of that form; republishing re-captures the current form.
    Stores {code, name, fields[]} — or null when the template has no (resolvable) input form."""
    code = (
        await session.execute(
            text("SELECT input_form_code FROM documents.templates WHERE id = :tid"),
            {"tid": template_id},
        )
    ).scalar_one_or_none()
    snapshot: dict[str, Any] | None = None
    if code:
        form = (
            (
                await session.execute(
                    text(
                        "SELECT id, code, name FROM documents.forms "
                        "WHERE code = :code AND tenant_id = auth.tenant_id()"
                    ),
                    {"code": code},
                )
            )
            .mappings()
            .one_or_none()
        )
        if form is not None:
            snapshot = {
                "code": form["code"],
                "name": form["name"],
                "fields": await _form_fields(session, form["id"]),
            }
    await session.execute(
        text(
            "UPDATE documents.template_versions SET input_form_snapshot = CAST(:snap AS jsonb) "
            "WHERE id = :id"
        ),
        {"snap": json.dumps(snapshot) if snapshot is not None else None, "id": version_id},
    )


@router.post("/{uid}/versions", name="save-template-version")
async def save_template_version(
    uid: str,
    body: SaveVersion,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> VersionDetailOut:
    """Save the editor's content (the editor autosaves here). Updates the head version in place
    while it's a fresh draft, so the autosave loop doesn't spawn a version per keystroke. It rolls
    forward to a new draft ``v(n+1)`` when the head is published (immutable) OR when the head draft
    is older than the 30-minute snapshot bucket — leaving the previous draft frozen as an automatic,
    time-bucketed restore point (Google-Docs-style version history). Returns the saved version."""
    require_permission(principal, TEMPLATES_WRITE)
    tid = await _template_id(session, uid)
    if tid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    # The head version is the newest. `stale` (computed on the DB clock) marks a draft whose bucket
    # has elapsed: a non-empty draft older than 30 minutes. When stale we roll to a fresh draft
    # instead of updating in place, freezing the old one as an automatic restore point.
    latest = (
        (
            await session.execute(
                text(
                    "SELECT id, version_number, status::text AS status, "
                    "  (status = 'draft' AND schema <> '{}'::jsonb "
                    "   AND now() - created_at >= interval '30 minutes') AS stale "
                    "FROM documents.template_versions "
                    "WHERE template_id = :tid ORDER BY version_number DESC LIMIT 1"
                ),
                {"tid": tid},
            )
        )
        .mappings()
        .one_or_none()
    )
    schema_json = json.dumps(body.schema_content)
    # Update the head draft in place unless it's published (immutable) or a stale draft (new
    # bucket); both of those start a fresh draft v(n+1).
    if latest is not None and latest["status"] == "draft" and not latest["stale"]:
        version_id = (
            await session.execute(
                text(
                    "UPDATE documents.template_versions "
                    "SET schema = CAST(:schema AS jsonb), renderer = COALESCE(:renderer, renderer) "
                    "WHERE id = :id RETURNING id"
                ),
                {"schema": schema_json, "renderer": body.renderer, "id": latest["id"]},
            )
        ).scalar_one()
    else:
        next_number = (latest["version_number"] + 1) if latest is not None else 1
        version_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                    "   created_by) "
                    "VALUES (:uid, auth.tenant_id(), :tid, :num, 'draft', "
                    "  COALESCE(:renderer, 'weasy')::documents.renderer, "
                    "  CAST(:schema AS jsonb), auth.uid()) RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "tid": tid,
                    "num": next_number,
                    "renderer": body.renderer,
                    "schema": schema_json,
                },
            )
        ).scalar_one()
    return await _version_detail_by_id(session, version_id)


def _has_content(schema: dict[str, Any]) -> bool:
    """The saved version has content when the editor's document carries at least one block.

    Two shapes are accepted: the GrapesJS persist blob the editor actually saves — content lives at
    ``gjs_project.pages[].frames[].component.components`` (the root frame's children) — and the
    legacy ``pages[].blocks`` TemplateContent shape (kept for forward-compat + the tests)."""
    if not isinstance(schema, dict):
        return False

    # GrapesJS blob: a frame's root component has at least one child component.
    project = schema.get("gjs_project")
    if isinstance(project, dict):
        pages = project.get("pages")
        if isinstance(pages, list):
            for page in pages:
                frames = page.get("frames") if isinstance(page, dict) else None
                for frame in frames if isinstance(frames, list) else []:
                    comp = frame.get("component") if isinstance(frame, dict) else None
                    children = comp.get("components") if isinstance(comp, dict) else None
                    if isinstance(children, list) and children:
                        return True

    # Legacy TemplateContent: any page carrying blocks.
    legacy_pages = schema.get("pages")
    if isinstance(legacy_pages, list):
        return any(isinstance(p, dict) and p.get("blocks") for p in legacy_pages)
    return False


class PublishResultOut(VersionDetailOut):
    # The published version, plus a soft warning: catalogue variables the version references that
    # won't fill (hand-typed / mistyped tokens). Non-blocking — publish still succeeds; the editor
    # surfaces them so advisers can tidy drift.
    unknown_variables: list[UnknownVariable] = []


@router.post("/{uid}/versions/{vid}/publish", name="publish-template-version")
async def publish_template_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> PublishResultOut:
    """Publish a draft version: validate it has content, archive the prior published version, mark
    this one published, and point the template at it. Publishing is not blocked by unknown
    variables — they come back as a soft warning (``unknown_variables``) for the editor to show."""
    require_permission(principal, TEMPLATES_WRITE)
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.template_id, v.status::text AS status, v.schema "
                    "FROM documents.template_versions v "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid AND v.uid = :vid"
                ),
                {"uid": uid, "vid": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    if row["status"] != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a draft version can be published."
        )
    if not _has_content(row["schema"]):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add at least one block before publishing.",
        )
    # Archive the currently-published version(s) of this template (one active at a time).
    await session.execute(
        text(
            "UPDATE documents.template_versions SET status = 'archived' "
            "WHERE template_id = :tid AND status = 'published' AND id <> :id"
        ),
        {"tid": row["template_id"], "id": row["id"]},
    )
    await session.execute(
        text(
            "UPDATE documents.template_versions "
            "SET status = 'published', published_at = now(), published_by = auth.uid() "
            "WHERE id = :id"
        ),
        {"id": row["id"]},
    )
    await session.execute(
        text("UPDATE documents.templates SET current_published_version_id = :id WHERE id = :tid"),
        {"id": row["id"], "tid": row["template_id"]},
    )
    # Freeze the linked input form onto this version — the published template won't change when the
    # form is later edited (republish re-captures).
    await _capture_form_snapshot(session, row["template_id"], row["id"])
    detail = await _version_detail_by_id(session, row["id"])
    # Soft warning: variables the published content references that won't fill (non-blocking drift).
    unknown = await unresolved_variables(session, uid, row["schema"])
    return PublishResultOut(**detail.model_dump(by_alias=True), unknown_variables=unknown)
