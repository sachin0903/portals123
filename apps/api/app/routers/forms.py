"""Forms — the staff-authored dynamic-form surface (builder persistence).

Firm staff author typed, ordered, conditional forms (questionnaires / fact-finds) in the platform
builder. This is the persistence those definitions were drawn to mirror (docs/forms.md): a form
record (``documents.forms``) plus its ordered field list (``documents.form_fields``), both
tenant-scoped. Runs under ``tenant_session`` (RLS ``authenticated`` enforces tenant isolation);
each action is gated by a ``forms:*`` permission.

Clients don't read forms through THIS router (RLS is staff tenant-lineage). A client filling a
``complete_form`` request item gets the form definition from the ownership-checked
``GET /documents/requests/{req}/items/{item}/form`` (in the documents router) instead. See
docs/forms.md and docs/api.md.
"""

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, require_any_permission, require_permission
from app.db import target_tenant_session as tenant_session
from app.flows.scoring import ScoringInvalid, apply_scoring, parse_scoring
from app.ids import new_id
from app.permissions import DOCUMENTS_READ, FORMS_READ, FORMS_SUBMIT, FORMS_WRITE

router = APIRouter(prefix="/forms", tags=["forms"])

# A form + its live field count + the creator's display name (created_by is a uuid on the wire).
_SELECT = (
    "SELECT f.uid, f.code, f.name, f.description, f.status, f.entity_type, f.document_type, "
    "       f.questionnaire_dataset_code, f.scoring, f.created_at, u.name AS created_by, "
    "       (SELECT count(*) FROM documents.form_fields ff WHERE ff.form_id = f.id) AS field_count "
    "FROM documents.forms f "
    "LEFT JOIN auth.users u ON u.id = f.created_by "
)

_FIELD_SELECT = (
    "SELECT uid, field_key, field_type, label, section, description, required, "
    "       conditions, validation, options, sort_order "
    "FROM documents.form_fields WHERE form_id = :fid ORDER BY sort_order, id"
)


class FormOut(BaseModel):
    uid: str
    code: str
    name: str
    description: str | None
    status: str
    entity_type: str
    document_type: str | None
    questionnaire_dataset_code: str | None
    scoring: Any | None
    field_count: int
    created_at: datetime
    created_by: str | None


class FormFieldIn(BaseModel):
    # uid is optional: persisted fields (uid starts 'fld_') keep their id across saves; anything
    # else (blank, or a 'fld-new-…' builder placeholder) gets a fresh one.
    uid: str | None = None
    field_key: str
    field_type: str = "text"
    label: str = ""
    section: str | None = None
    description: str | None = None
    required: bool = False
    conditions: Any | None = None
    validation: Any | None = None
    options: Any | None = None
    sort_order: int = 0


class FormFieldOut(BaseModel):
    uid: str
    field_key: str
    field_type: str
    label: str
    section: str | None
    description: str | None
    required: bool
    conditions: Any | None
    validation: Any | None
    options: Any | None
    sort_order: int


class CreateForm(BaseModel):
    code: str
    name: str
    entity_type: Literal["applicant", "account"] = "applicant"
    document_type: str | None = None
    description: str | None = None
    questionnaire_dataset_code: str | None = None


class UpdateForm(BaseModel):
    # All optional — a PATCH updates only the fields provided. `status` is deliberately NOT here:
    # lifecycle transitions run only through the dedicated endpoints (publish → published, delete →
    # archived with its in-use guard), so a metadata PATCH can't archive a form mid-fill or bypass
    # the publish validation.
    name: str | None = None
    code: str | None = None
    description: str | None = None
    entity_type: Literal["applicant", "account"] | None = None
    document_type: str | None = None
    questionnaire_dataset_code: str | None = None
    # Present only when the builder saved the scoring panel. Absent leaves the stored
    # document alone; null clears it. The shape is checked in the handler.
    scoring: Any | None = None


class SetFields(BaseModel):
    fields: list[FormFieldIn]


class FormResponseIn(BaseModel):
    answers: dict[str, Any]


class FormResponseOut(BaseModel):
    uid: str
    form_uid: str
    answers: dict[str, Any]
    outputs: dict[str, Any]
    submitted_at: datetime
    submitted_by: str | None


class FormResponseListItem(BaseModel):
    """One submission in the form's responses list. `via` distinguishes a client's request-driven
    fill ('request') from a staff standalone submission ('standalone')."""

    uid: str
    submitted_at: datetime
    submitted_by: str | None
    via: str
    answers: dict[str, Any]
    outputs: dict[str, Any]


async def _fetch(session: AsyncSession, uid: str) -> FormOut | None:
    row = (
        (await session.execute(text(_SELECT + "WHERE f.uid = :uid"), {"uid": uid}))
        .mappings()
        .one_or_none()
    )
    return FormOut(**row) if row else None


async def _form_id(session: AsyncSession, uid: str) -> int | None:
    """The internal id for a form uid, RLS-scoped (None if not visible/absent)."""
    return (
        await session.execute(text("SELECT id FROM documents.forms WHERE uid = :uid"), {"uid": uid})
    ).scalar_one_or_none()


@router.get("", name="list-forms")
async def list_forms(
    # Searched in SQL rather than in the browser: the page filters `name` and `code`, and
    # doing that here is what makes its question reach past the rows already sent.
    q: str | None = None,
    include_archived: bool = False,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[FormOut]:
    """The firm's forms (RLS-scoped), newest first. Excludes archived unless ``include_archived``.

    Any staff member with ``forms:read`` can list archived forms."""
    require_permission(principal, FORMS_READ)
    clauses = [] if include_archived else ["f.status <> 'archived'"]
    params: dict[str, str] = {}
    if q and q.strip():
        clauses.append("(f.name ILIKE :q OR f.code ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
    rows = (
        (
            await session.execute(
                # `where` is fixed text plus bound parameters — no caller string reaches the SQL.
                text(_SELECT + where + "ORDER BY f.created_at DESC, f.id DESC"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )
    return [FormOut(**r) for r in rows]


@router.post("", name="create-form", status_code=status.HTTP_201_CREATED)
async def create_form(
    body: CreateForm,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """Create a form as an initial draft. ``code`` must be unique within the firm."""
    require_permission(principal, FORMS_WRITE)
    form_uid = new_id()
    try:
        await session.execute(
            text(
                "INSERT INTO documents.forms "
                "  (uid, tenant_id, code, name, description, entity_type, document_type, "
                "   questionnaire_dataset_code, created_by) "
                "VALUES (:uid, auth.tenant_id(), :code, :name, :desc, :entity, :dtype, "
                "  :qds, auth.uid())"
            ),
            {
                "uid": form_uid,
                "code": body.code,
                "name": body.name,
                "desc": body.description,
                "entity": body.entity_type,
                "dtype": body.document_type,
                "qds": body.questionnaire_dataset_code,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A form with this code already exists.",
        ) from exc
    form = await _fetch(session, form_uid)
    if form is None:  # pragma: no cover - just inserted under this tenant
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Create failed"
        )
    return form


@router.get("/{uid}", name="get-form")
async def get_form(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """One form's metadata (RLS-scoped)."""
    require_permission(principal, FORMS_READ)
    form = await _fetch(session, uid)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form


@router.patch("/{uid}", name="update-form")
async def update_form(
    uid: str,
    body: UpdateForm,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """Update a form's metadata (name / code / description / entity / doc-type / scoring). Status
    is not editable here — publish/archive are dedicated transitions. ``scoring`` is written only
    when the request carries it, so a rename does not clear a score configuration."""
    require_permission(principal, FORMS_WRITE)
    scoring_value: str | None = None
    write_scoring = False
    if "scoring" in body.model_fields_set:
        try:
            parsed = parse_scoring(body.scoring)
        except ScoringInvalid as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        write_scoring = True
        scoring_value = json.dumps(parsed) if parsed is not None else None
    # Two statements rather than an interpolated fragment: `scoring` is written only when the
    # request carried it, and the SQL text itself stays a literal.
    update_sql = (
        "UPDATE documents.forms SET "
        "  scoring = CAST(:scoring AS jsonb), "
        "  name = COALESCE(:name, name), "
        "  code = COALESCE(:code, code), "
        "  description = COALESCE(:desc, description), "
        "  entity_type = COALESCE(:entity, entity_type), "
        "  document_type = COALESCE(:dtype, document_type), "
        "  questionnaire_dataset_code = COALESCE(:qds, questionnaire_dataset_code), "
        "  updated_at = now() "
        "WHERE uid = :uid RETURNING id"
        if write_scoring
        else "UPDATE documents.forms SET "
        "  name = COALESCE(:name, name), "
        "  code = COALESCE(:code, code), "
        "  description = COALESCE(:desc, description), "
        "  entity_type = COALESCE(:entity, entity_type), "
        "  document_type = COALESCE(:dtype, document_type), "
        "  questionnaire_dataset_code = COALESCE(:qds, questionnaire_dataset_code), "
        "  updated_at = now() "
        "WHERE uid = :uid RETURNING id"
    )
    try:
        row = (
            await session.execute(
                text(update_sql),
                {
                    "name": body.name,
                    "code": body.code,
                    "desc": body.description,
                    "entity": body.entity_type,
                    "dtype": body.document_type,
                    "qds": body.questionnaire_dataset_code,
                    "scoring": scoring_value,
                    "uid": uid,
                },
            )
        ).first()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A form with this code already exists.",
        ) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    form = await _fetch(session, uid)
    if form is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed"
        )
    return form


@router.post("/{uid}/publish", name="publish-form")
async def publish_form(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """Publish a form so it can back a request or a questionnaire dataset. Validated transition:
    the form must exist, not be archived, and carry at least one field (a client can't be asked to
    fill an empty form). Idempotent for an already-published form."""
    require_permission(principal, FORMS_WRITE)
    row = (
        (
            await session.execute(
                text(
                    "SELECT f.id, f.status, "
                    "  (SELECT count(*) FROM documents.form_fields ff WHERE ff.form_id = f.id) "
                    "    AS field_count "
                    "FROM documents.forms f WHERE f.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    if row["status"] == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An archived form can't be published."
        )
    if row["field_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add at least one field before publishing.",
        )
    await session.execute(
        text("UPDATE documents.forms SET status = 'published', updated_at = now() WHERE id = :id"),
        {"id": row["id"]},
    )
    form = await _fetch(session, uid)
    if form is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Publish failed"
        )
    return form


@router.delete("/{uid}", name="delete-form")
async def delete_form(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """Archive a form (soft — status='archived'): it drops off the list but its fields and any
    responses are preserved, and its ``uid`` stays resolvable (a request may still reference it).

    A **reference guard** refuses (409) while any open (pending) request item still points at this
    form (``request_items.form_uid``) — archiving it would strand a client mid-fill. Cancel or
    fulfil those requests first. This replaces a hard delete, which would cascade the fields away
    and leave those request items dangling."""
    require_permission(principal, FORMS_WRITE)
    fid = await _form_id(session, uid)
    if fid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    in_use = (
        await session.execute(
            text(
                "SELECT count(*) FROM documents.request_items "
                "WHERE form_uid = :uid AND status = 'pending'"
            ),
            {"uid": uid},
        )
    ).scalar_one()
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This form is still requested from {in_use} client(s). "
                "Cancel or complete those requests before archiving it."
            ),
        )
    await session.execute(
        text("UPDATE documents.forms SET status = 'archived', updated_at = now() WHERE uid = :uid"),
        {"uid": uid},
    )
    form = await _fetch(session, uid)
    if form is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Archive failed"
        )
    return form


@router.post("/{uid}/unarchive", name="unarchive-form")
async def unarchive_form(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormOut:
    """Restore an archived form. It comes back as a **draft** (a revived form re-earns publication
    via the validated publish transition, rather than silently returning to published). Refuses
    (409) if the form isn't archived. Any staff member with ``forms:write`` can do this."""
    require_permission(principal, FORMS_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.forms SET status = 'draft', updated_at = now() "
                "WHERE uid = :uid AND status = 'archived' RETURNING id"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None:
        # Distinguish "no such form" (404) from "exists but not archived" (409).
        form = await _fetch(session, uid)
        if form is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This form is not archived."
        )
    form = await _fetch(session, uid)
    if form is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unarchive failed"
        )
    return form


@router.get("/{uid}/fields", name="get-form-fields")
async def get_form_fields(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[FormFieldOut]:
    """A form's ordered field list (RLS-scoped)."""
    require_permission(principal, FORMS_READ)
    fid = await _form_id(session, uid)
    if fid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    rows = (await session.execute(text(_FIELD_SELECT), {"fid": fid})).mappings().all()
    return [FormFieldOut(**r) for r in rows]


@router.put("/{uid}/fields", name="set-form-fields")
async def set_form_fields(
    uid: str,
    body: SetFields,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[FormFieldOut]:
    """Replace a form's whole field list in one call (the builder's save-all). ``sort_order`` is
    re-numbered by position; a field this form already owns keeps its id."""
    require_permission(principal, FORMS_WRITE)
    fid = await _form_id(session, uid)
    if fid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    # Which field uids this form actually owns, read BEFORE the delete-and-reinsert below. An
    # incoming uid is reused only if it is one of them, so a persisted field keeps its id and
    # anything else -- the builder's `fld-new-N` placeholder, or another form's field -- gets a
    # fresh one. This was a `startswith("fld_")` test, which accepted any string wearing the right
    # prefix INCLUDING another form's id; public ids carry no prefix now, so the question has to be
    # asked of the database, which is the stronger answer it always should have been.
    owned = {
        row[0]
        for row in await session.execute(
            text("SELECT uid FROM documents.form_fields WHERE form_id = :fid"), {"fid": fid}
        )
    }
    await session.execute(
        text("DELETE FROM documents.form_fields WHERE form_id = :fid"), {"fid": fid}
    )
    for index, field in enumerate(body.fields):
        field_uid = field.uid if field.uid in owned else new_id()
        await session.execute(
            text(
                "INSERT INTO documents.form_fields "
                "  (uid, tenant_id, form_id, field_key, field_type, label, section, description, "
                "   required, conditions, validation, options, sort_order) "
                "VALUES (:uid, auth.tenant_id(), :fid, :key, :type, :label, :section, :desc, "
                "  :required, CAST(:cond AS jsonb), CAST(:valid AS jsonb), CAST(:opts AS jsonb), "
                "  :sort)"
            ),
            {
                "uid": field_uid,
                "fid": fid,
                "key": field.field_key,
                "type": field.field_type,
                "label": field.label,
                "section": field.section,
                "desc": field.description,
                "required": field.required,
                "cond": json.dumps(field.conditions) if field.conditions is not None else None,
                "valid": json.dumps(field.validation) if field.validation is not None else None,
                "opts": json.dumps(field.options) if field.options is not None else None,
                "sort": index + 1,
            },
        )
    rows = (await session.execute(text(_FIELD_SELECT), {"fid": fid})).mappings().all()
    return [FormFieldOut(**r) for r in rows]


@router.post("/{uid}/responses", name="create-form-response", status_code=status.HTTP_201_CREATED)
async def create_form_response(
    uid: str,
    body: FormResponseIn,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FormResponseOut:
    """Record a standalone form submission (not tied to a client request). Answers are stored as a
    jsonb blob keyed by fieldKey; the submitter is the calling staff member. Request-driven client
    fills go through ``POST /documents/requests/.../form-response`` instead.

    Gated ``forms:submit`` (split out of ``forms:write``): submitting a response is using a form,
    not authoring one — form designers and form fillers are different roles."""
    require_permission(principal, FORMS_SUBMIT)
    fid = await _form_id(session, uid)
    if fid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    scoring = (
        await session.execute(
            text("SELECT scoring FROM documents.forms WHERE id = :fid"), {"fid": fid}
        )
    ).scalar_one_or_none()
    answers, outputs = apply_scoring(body.answers, scoring)
    resp_uid = new_id()
    row = (
        (
            await session.execute(
                text(
                    # Standalone (staff) submission: no client contact_id -- only request-driven
                    # client fills carry one. submitted_by records the staff actor. outputs are
                    # the server-computed band; a posted answer under the same key is already
                    # overwritten in `answers`.
                    "INSERT INTO documents.form_responses "
                    "  (uid, tenant_id, form_id, form_uid, answers, outputs, submitted_by) "
                    "VALUES (:uid, auth.tenant_id(), :fid, :form_uid, "
                    "  CAST(:answers AS jsonb), CAST(:outputs AS jsonb), auth.uid()) "
                    "RETURNING uid, form_uid, answers, outputs, submitted_at, "
                    "  (SELECT name FROM auth.users WHERE id = submitted_by) AS submitted_by"
                ),
                {
                    "uid": resp_uid,
                    "fid": fid,
                    "form_uid": uid,
                    "answers": json.dumps(answers),
                    "outputs": json.dumps(outputs),
                },
            )
        )
        .mappings()
        .one()
    )
    return FormResponseOut(**row)


@router.get("/{uid}/responses", name="list-form-responses")
async def list_form_responses(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[FormResponseListItem]:
    """Submissions received for a form (RLS-scoped), newest first — both client request-driven fills
    and staff standalone submissions. Lets the form author see what has actually come back; without
    it the stored answers are write-only. Keyed by ``form_uid`` so it catches request-driven rows
    (which carry no ``form_id``) as well as standalone ones."""
    # Deliberately NOT `forms:read`: that gates the form DEFINITIONS, and responses are client
    # data — a template designer browsing the form library must not thereby read every fact-find
    # answer in the firm. `forms:submit` covers the response plane (submitting and seeing what
    # came back travel together); `documents:read` covers the client-paperwork readers, since a
    # request-driven fill is part of the client's document trail they already see.
    require_any_permission(principal, FORMS_SUBMIT, DOCUMENTS_READ)
    fid = await _form_id(session, uid)
    if fid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    rows = (
        (
            await session.execute(
                text(
                    "SELECT fr.uid, fr.submitted_at, u.name AS submitted_by, fr.answers, "
                    "  fr.outputs, "
                    "  CASE WHEN fr.request_item_id IS NULL THEN 'standalone' ELSE 'request' END "
                    "    AS via "
                    "FROM documents.form_responses fr "
                    "LEFT JOIN auth.users u ON u.id = fr.submitted_by "
                    "WHERE fr.form_uid = :uid "
                    "ORDER BY fr.submitted_at DESC, fr.id DESC"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .all()
    )
    return [FormResponseListItem(**r) for r in rows]
