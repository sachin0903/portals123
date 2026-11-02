"""Does a submission actually answer the form? — the one implementation of that question.

It lives beside the orchestrator rather than in a router because the rule has to hold at the
**transition**, not at one entry point: a ``fill_form`` step is submitted by staff
(``POST /applications/{uid}/cases/{case}/steps/{key}/form``) *and* by the client
(``POST /documents/requests/{req}/items/{item}/form-response``), and a fact-find that completes with
nothing recorded is silent data loss whichever door it came through.

Conditional fields are exempt: their condition may legitimately be false for this client, and the
condition language is evaluated in the browser, not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import national_insurance


def is_blank(value: Any, field_type: str | None = None) -> bool:
    """An answer that carries nothing. ``0``/``False`` ARE answers; ``''``/``[]``/``{}``/None are
    not."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if field_type == "address" and isinstance(value, Mapping):
        # A composite address object is not answered merely because its shell exists. The browser
        # applies the same four-part rule, but the transition is the authoritative boundary because
        # clients can submit through more than one route.
        return any(
            not isinstance(value.get(key), str) or len(value[key].strip()) < 2
            for key in ("line1", "city", "postcode", "country")
        )
    if field_type == "phoneNumbers" and isinstance(value, list):
        # A list of rows is not an answer merely because rows exist. The editor drops blank rows
        # before it posts, and the transition is the authoritative boundary because more than one
        # door reaches it — the same argument the address rule above makes.
        return not any(
            isinstance(entry, Mapping) and str(entry.get("number") or "").strip() for entry in value
        )
    if isinstance(value, (list, dict)):
        return not value
    return False


def repeating_columns(options: Any) -> list[dict[str, Any]]:
    """The column list on a ``repeating`` field's ``options``.

    Each column is ``{key, label, type}``. ``required`` defaults to true — a column the
    author did not mark optional is a required column. ``type`` is a scalar field type
    the renderer already has (``text``, ``number``, ``currency``, ``date``, ``select``, …).
    """
    if not isinstance(options, list):
        return []
    columns: list[dict[str, Any]] = []
    for item in options:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        label = item.get("label")
        column_type = item.get("type")
        required = item.get("required")
        columns.append(
            {
                "key": key.strip(),
                "label": str(label).strip()
                if isinstance(label, str) and label.strip()
                else key.strip(),
                "type": column_type if isinstance(column_type, str) and column_type else "text",
                "required": True if required is None else bool(required),
            }
        )
    return columns


def repeating_gap(field: Mapping[str, Any], value: Any) -> str | None:
    """The label to report when a repeating answer fails, or ``None`` when it is acceptable.

    A required group with no row fails as the group. A required column left blank on any
    row fails as ``"<group> (<column>)"`` — including a row on an optional group, because
    starting a row and leaving a required cell empty is not an answer. A column is required
    unless it says ``required: false``.
    """
    columns = repeating_columns(field.get("options"))
    label = str(field.get("label") or field.get("field_key") or "This field")
    rows = value if isinstance(value, list) else None
    if not rows:
        return label if field.get("required") else None
    for row in rows:
        if not isinstance(row, Mapping):
            blank = next((col for col in columns if col["required"]), None)
            return f"{label} ({blank['label']})" if blank is not None else label
        for column in columns:
            if not column["required"]:
                continue
            if is_blank(row.get(column["key"]), column["type"]):
                return f"{label} ({column['label']})"
    return None


def missing_required(
    fields: Sequence[Mapping[str, Any]], answers: Mapping[str, Any] | None
) -> list[str]:
    """Labels of required, non-conditional fields this submission leaves unanswered or invalid.

    ``fields`` rows carry ``field_key``/``label``/``required``/``conditions``/``options`` (the
    shape ``documents.form_fields`` returns). ``answers`` of ``None`` — no submission at all — is
    treated as answering nothing, so an absent payload is as invalid as an empty one.

    A ``repeating`` field is the exception to "optional means never demanded": an optional group
    may be empty, but a required column left blank on a row that was started is still missing.
    ``repeating_gap`` encodes that. Conditional fields stay exempt — their condition is evaluated
    in the browser, not here.
    """
    supplied: Mapping[str, Any] = answers if isinstance(answers, Mapping) else {}
    missing: list[str] = []
    for field in fields:
        if field["conditions"]:
            continue
        key = str(field["field_key"])
        value = supplied.get(key)
        if str(field.get("field_type") or "") == "repeating":
            gap = repeating_gap(field, value)
            if gap:
                missing.append(gap)
            continue
        if not field["required"]:
            continue
        invalid = is_blank(value, str(field.get("field_type") or ""))
        validation = field.get("validation")
        if (
            not invalid
            and isinstance(value, str)
            and isinstance(validation, Mapping)
            and isinstance(validation.get("minLength"), int)
        ):
            invalid = len(value.strip()) < int(validation["minLength"])
        if invalid:
            missing.append(str(field["label"] or key))
    return missing


async def missing_required_answers(
    session: AsyncSession,
    *,
    tenant_id: int,
    form_uid: str | None,
    answers: Mapping[str, Any] | None,
) -> list[str]:
    """``missing_required`` against the live form definition.

    An unknown/blank ``form_uid`` yields no fields and therefore no complaint — there is no form to
    judge the submission against, and inventing a refusal there would wedge legacy request items
    whose form code was never recorded.
    """
    if not form_uid:
        return []
    fields = (
        (
            await session.execute(
                text(
                    "SELECT ff.field_key, ff.field_type, ff.label, ff.required, ff.conditions, "
                    "       ff.validation, ff.options "
                    "FROM documents.form_fields ff JOIN documents.forms f ON f.id = ff.form_id "
                    "WHERE f.uid = :u AND f.tenant_id = :t ORDER BY ff.sort_order, ff.id"
                ),
                {"u": form_uid, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return missing_required(cast("Sequence[Mapping[str, Any]]", fields), answers)


def national_insurance_problems(
    fields: Sequence[Mapping[str, Any]], answers: Mapping[str, Any] | None
) -> list[str]:
    """Sentences for a National Insurance number that is present but not one HMRC would issue.

    A blank number is left to ``missing_required``. The check follows the identifier TYPE the
    form stored beside the value, so a passport or a CONCAT on the same field is left alone.
    A field with a display condition is not judged here — that condition is evaluated in the
    browser, the same rule ``missing_required`` uses.
    """
    supplied: Mapping[str, Any] = answers if isinstance(answers, Mapping) else {}
    if not national_insurance.is_nino_type(supplied.get("national_identifier_type")):
        return []
    problems: list[str] = []
    for field in fields:
        if str(field["field_key"]) != "national_identifier":
            continue
        if field.get("conditions"):
            continue
        value = supplied.get("national_identifier")
        if is_blank(value) or national_insurance.is_valid(value):
            continue
        label = str(field["label"] or "national_identifier")
        problems.append(f"{label}: {national_insurance.FIELD_MESSAGE}")
    return problems


async def national_insurance_in_use(
    session: AsyncSession,
    *,
    tenant_id: int,
    answers: Mapping[str, Any] | None,
    contact_id: int | None,
) -> str | None:
    """The plain refusal when another client in the firm already holds this number."""
    if not isinstance(answers, Mapping):
        return None
    if not national_insurance.is_nino_type(answers.get("national_identifier_type")):
        return None
    compact = national_insurance.normalise(answers.get("national_identifier"))
    if compact is None:
        return None
    taken = (
        await session.execute(
            text(
                "SELECT 1 FROM crm.contacts "
                "WHERE tenant_id = :t "
                "  AND upper(translate(coalesce(national_identifier, ''), ' -', '')) = :ni "
                "  AND (CAST(:cid AS int) IS NULL OR id <> CAST(:cid AS int)) "
                "LIMIT 1"
            ),
            {"t": tenant_id, "ni": compact, "cid": contact_id},
        )
    ).first()
    return national_insurance.IN_USE if taken else None


async def national_insurance_refusal(
    session: AsyncSession,
    *,
    tenant_id: int,
    form_uid: str | None,
    answers: Mapping[str, Any] | None,
    contact_id: int | None,
) -> str | None:
    """Why this National Insurance number cannot be stored, or None when it can."""
    if not form_uid:
        return None
    fields = (
        (
            await session.execute(
                text(
                    "SELECT ff.field_key, ff.field_type, ff.label, ff.required, ff.conditions, "
                    "       ff.validation, ff.options "
                    "FROM documents.form_fields ff JOIN documents.forms f ON f.id = ff.form_id "
                    "WHERE f.uid = :u AND f.tenant_id = :t ORDER BY ff.sort_order, ff.id"
                ),
                {"u": form_uid, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    problems = national_insurance_problems(cast("Sequence[Mapping[str, Any]]", fields), answers)
    if problems:
        return " ".join(problems[:4])
    return await national_insurance_in_use(
        session, tenant_id=tenant_id, answers=answers, contact_id=contact_id
    )
