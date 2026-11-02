"""Flow export and import — one flow as a file, and that file back into a firm.

A flow definition is not portable on its own: a ``fill_form`` step names its form by **uid** and a
``generate_document`` step names its template by uid, and uids are minted per tenant. So the file
this module writes carries the definition **and** the forms and templates it points at, each by
its **code**, with a ``refs`` map from the uid in the definition to that code. Import resolves
every ref in the receiving firm: a form or template with the same code is reused as it is; one
that is missing is created from the bundle; then the definition is rewritten to the receiving
firm's uids and saved as a **draft** version. Nothing is published by an import — publish is the
author's act, and it runs every reference check the engine has.

What is NOT carried, and is warned about instead: a document a step hands out (``document_uid``,
a stored file), a term (``term_uid`` — resolved by code when the firm has one), and the catalogue
ids a ``setup_account`` step holds (``product_id``, ``model_id``, ``fee_package_id``), which name
rows in the firm's own catalogue. A raise-a-task spec assigned to a **user** is warned about too;
one assigned to a role travels by slug and needs no help.

Format: ``{"format": "graphene.flow", "version": 1, ...}``. The version is bumped when the shape
changes incompatibly; an import refuses a format or version it does not know.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import definitions
from app.ids import new_id

FORMAT = "graphene.flow"
FORMAT_VERSION = 1

#: The config keys that name another record by uid, and the kind of record each names.
_REF_KEYS: dict[str, str] = {
    "form_uid": "form",
    "template_uid": "template",
    "term_uid": "term",
    "document_uid": "document",
}
_CATALOGUE_IDS = ("product_id", "model_id", "fee_package_id")


class BundleError(ValueError):
    """The file is not a flow bundle this build can read."""


def _configs(definition: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every ``(label, config)`` in the definition — nodes when it is a graph, else the legacy
    steps — so a rewrite reaches each place a uid can sit. The derived ``steps`` projection of a
    graph is included too, because an exported definition carries it verbatim."""
    out: list[tuple[str, dict[str, Any]]] = []
    for key in ("nodes", "steps"):
        rows = definition.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            config = row.get("config")
            if isinstance(config, dict):
                label = str(row.get("title") or row.get("id") or "a step")
                out.append((label, config))
    return out


def referenced_uids(definition: dict[str, Any]) -> dict[str, set[str]]:
    """The uids the definition names, by kind: ``{"form": {...}, "template": {...}, ...}``."""
    found: dict[str, set[str]] = {kind: set() for kind in _REF_KEYS.values()}
    for _label, config in _configs(definition):
        for key, kind in _REF_KEYS.items():
            value = config.get(key)
            if isinstance(value, str) and value:
                found[kind].add(value)
    return found


def rewrite_refs(definition: dict[str, Any], mapping: dict[str, dict[str, str]]) -> dict[str, Any]:
    """A copy of the definition with every known uid replaced: ``mapping[kind][old] = new``."""
    out: dict[str, Any] = json.loads(json.dumps(definition))
    for _label, config in _configs(out):
        for key, kind in _REF_KEYS.items():
            value = config.get(key)
            if isinstance(value, str) and value in mapping.get(kind, {}):
                config[key] = mapping[kind][value]
    return out


def portability_warnings(definition: dict[str, Any], unresolved: dict[str, set[str]]) -> list[str]:
    """What the receiving firm has to check by hand after an import. Each warning once: a graph
    definition carries every step twice (``nodes`` and the derived ``steps``), and the sheet that
    lists these keys by the text."""
    return list(dict.fromkeys(_warnings(definition, unresolved)))


def _warnings(definition: dict[str, Any], unresolved: dict[str, set[str]]) -> list[str]:
    warnings: list[str] = []
    for label, config in _configs(definition):
        for key, kind in _REF_KEYS.items():
            value = config.get(key)
            if isinstance(value, str) and value in unresolved.get(kind, set()):
                if kind == "document":
                    warnings.append(
                        f"“{label}” hands out a stored document, which does not travel with a "
                        "flow. Choose the document again."
                    )
                elif kind == "term":
                    warnings.append(
                        f"“{label}” names a term this firm does not have. Choose the term again."
                    )
                else:
                    warnings.append(
                        f"“{label}” names a {kind} that was not in the file. Choose it again."
                    )
        held = [key for key in _CATALOGUE_IDS if config.get(key) is not None]
        if held:
            warnings.append(
                f"“{label}” names {', '.join(held)} from the exporting firm's catalogue. Choose "
                "the product, model and fee package again."
            )
    for row in _task_specs(definition):
        assign = row.get("assign_to") if isinstance(row, dict) else None
        if isinstance(assign, dict) and assign.get("kind") == "user":
            warnings.append(
                f"A task “{row.get('title') or 'task'}” is assigned to a user from the exporting "
                "firm. Choose who it goes to again."
            )
    return warnings


def _task_specs(definition: dict[str, Any]) -> list[Any]:
    specs: list[Any] = []
    for key in ("nodes", "steps"):
        rows = definition.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("tasks"), list):
                specs.extend(row["tasks"])
    return specs


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ==================================================================================================
# Export
# ==================================================================================================
async def export_bundle(session: AsyncSession, flow_uid: str) -> dict[str, Any] | None:
    """The flow as a file: its published definition when it has one, else its latest draft, plus
    every form and template that definition names. ``None`` when the flow is not visible."""
    flow = (
        (
            await session.execute(
                text(
                    "SELECT f.id, f.code, f.name, f.description, f.entity, "
                    "       f.current_published_version_id FROM flows.flows f WHERE f.uid = :u"
                ),
                {"u": flow_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if flow is None:
        return None
    version = (
        (
            await session.execute(
                text(
                    "SELECT version, status, definition FROM flows.flow_versions "
                    "WHERE flow_id = :f "
                    "ORDER BY (id = :published) DESC, version DESC LIMIT 1"
                ),
                {"f": flow["id"], "published": flow["current_published_version_id"] or -1},
            )
        )
        .mappings()
        .one_or_none()
    )
    definition: dict[str, Any] = _json(version["definition"]) if version else {}
    if not isinstance(definition, dict):
        definition = {}
    refs = referenced_uids(definition)
    forms = await _export_forms(session, sorted(refs["form"]))
    templates = await _export_templates(session, sorted(refs["template"]))
    terms = await _term_codes(session, sorted(refs["term"]))
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "flow": {
            "code": flow["code"],
            "name": flow["name"],
            "description": flow["description"],
            "entity": flow["entity"],
        },
        "source": (
            {"version": int(version["version"]), "status": str(version["status"])}
            if version
            else None
        ),
        "definition": definition,
        "refs": {
            "forms": {uid: form["code"] for uid, form in forms.items()},
            "templates": {uid: tpl["code"] for uid, tpl in templates.items()},
            "terms": terms,
        },
        "forms": list(forms.values()),
        "templates": list(templates.values()),
    }


async def _export_forms(session: AsyncSession, uids: list[str]) -> dict[str, dict[str, Any]]:
    if not uids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, code, name, description, status, entity_type, scoring "
                    "FROM documents.forms WHERE uid = ANY(:u) ORDER BY code"
                ),
                {"u": uids},
            )
        )
        .mappings()
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        fields = (
            (
                await session.execute(
                    text(
                        "SELECT field_key, field_type, label, section, description, required, "
                        "       conditions, validation, options FROM documents.form_fields "
                        "WHERE form_id = :f ORDER BY sort_order, id"
                    ),
                    {"f": row["id"]},
                )
            )
            .mappings()
            .all()
        )
        out[str(row["uid"])] = {
            "code": row["code"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "entity_type": row["entity_type"],
            "scoring": _json(row["scoring"]),
            "fields": [
                {
                    "field_key": f["field_key"],
                    "field_type": f["field_type"],
                    "label": f["label"],
                    "section": f["section"],
                    "description": f["description"],
                    "required": bool(f["required"]),
                    "conditions": _json(f["conditions"]),
                    "validation": _json(f["validation"]),
                    "options": _json(f["options"]),
                }
                for f in fields
            ],
        }
    return out


async def _export_templates(session: AsyncSession, uids: list[str]) -> dict[str, dict[str, Any]]:
    """Each template with its published version's body, else its latest version's."""
    if not uids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.uid, t.code, t.name, t.document_type, t.description, "
                    "       v.status, v.renderer, v.schema "
                    "FROM documents.templates t "
                    "LEFT JOIN LATERAL ("
                    "  SELECT status, renderer, schema FROM documents.template_versions v "
                    "  WHERE v.template_id = t.id "
                    "  ORDER BY (v.id = t.current_published_version_id) DESC, "
                    "    v.version_number DESC LIMIT 1"
                    ") v ON true "
                    "WHERE t.uid = ANY(:u) ORDER BY t.code"
                ),
                {"u": uids},
            )
        )
        .mappings()
        .all()
    )
    return {
        str(row["uid"]): {
            "code": row["code"],
            "name": row["name"],
            "document_type": row["document_type"],
            "description": row["description"],
            "status": str(row["status"] or "draft"),
            "renderer": str(row["renderer"] or "weasy"),
            "schema": _json(row["schema"]) or {},
        }
        for row in rows
    }


async def _term_codes(session: AsyncSession, uids: list[str]) -> dict[str, str]:
    if not uids:
        return {}
    rows = (
        await session.execute(
            text("SELECT uid, code FROM terms.terms WHERE uid = ANY(:u)"), {"u": uids}
        )
    ).all()
    return {str(uid): str(code) for uid, code in rows}


# ==================================================================================================
# Import
# ==================================================================================================
def read_bundle(raw: Any) -> dict[str, Any]:
    """Check the file is a bundle this build reads, and that it carries a definition."""
    if not isinstance(raw, dict):
        raise BundleError("This file is not a flow export.")
    if raw.get("format") != FORMAT:
        raise BundleError("This file is not a flow export.")
    if raw.get("version") != FORMAT_VERSION:
        raise BundleError(
            f"This flow export is version {raw.get('version')!r}; this build reads version "
            f"{FORMAT_VERSION}."
        )
    flow = raw.get("flow")
    if not isinstance(flow, dict) or not isinstance(flow.get("code"), str) or not flow["code"]:
        raise BundleError("The export does not name its flow.")
    if not isinstance(raw.get("definition"), dict):
        raise BundleError("The export carries no flow definition.")
    return raw


async def import_bundle(
    session: AsyncSession, raw: Any, *, code: str | None = None
) -> dict[str, Any]:
    """Bring a bundle into the calling firm (``tenant_session``; RLS scopes every read and write).

    Forms and templates are matched by code and created when missing. The definition is rewritten
    to this firm's uids, validated, and saved as a draft: a new flow when the code is free, else
    the next draft version of the flow that already has it. ``code`` overrides the file's.

    Returns ``{"flow_uid", "created", "version_uid", "forms": {"created", "reused"},
    "templates": {"created", "reused"}, "warnings"}``.
    """
    bundle = read_bundle(raw)
    refs: dict[str, Any] = bundle["refs"] if isinstance(bundle.get("refs"), dict) else {}
    forms_by_code = {
        str(f["code"]): f
        for f in (bundle.get("forms") or [])
        if isinstance(f, dict) and isinstance(f.get("code"), str)
    }
    templates_by_code = {
        str(t["code"]): t
        for t in (bundle.get("templates") or [])
        if isinstance(t, dict) and isinstance(t.get("code"), str)
    }

    mapping: dict[str, dict[str, str]] = {"form": {}, "template": {}, "term": {}}
    report: dict[str, dict[str, list[str]]] = {
        "forms": {"created": [], "reused": []},
        "templates": {"created": [], "reused": []},
    }
    for old_uid, form_code in (refs.get("forms") or {}).items():
        spec = forms_by_code.get(str(form_code))
        uid, created = await _ensure_form(session, str(form_code), spec)
        if uid is None:
            continue
        mapping["form"][str(old_uid)] = uid
        report["forms"]["created" if created else "reused"].append(str(form_code))
    for old_uid, template_code in (refs.get("templates") or {}).items():
        spec = templates_by_code.get(str(template_code))
        uid, created = await _ensure_template(session, str(template_code), spec)
        if uid is None:
            continue
        mapping["template"][str(old_uid)] = uid
        report["templates"]["created" if created else "reused"].append(str(template_code))
    for old_uid, term_code in (refs.get("terms") or {}).items():
        uid = (
            await session.execute(
                text(
                    "SELECT uid FROM terms.terms WHERE code = :c AND tenant_id = auth.tenant_id() "
                    "ORDER BY (status = 'published') DESC, id DESC LIMIT 1"
                ),
                {"c": str(term_code)},
            )
        ).scalar_one_or_none()
        if uid is not None:
            mapping["term"][str(old_uid)] = str(uid)

    original = bundle["definition"]
    rewritten = rewrite_refs(original, mapping)
    wanted = referenced_uids(original)
    unresolved = {
        kind: {uid for uid in uids if uid not in mapping.get(kind, {})}
        for kind, uids in wanted.items()
    }
    # Said in terms of the rewritten definition, which is the one the author will look at.
    warnings = portability_warnings(rewritten, unresolved)
    definition = definitions.validate_definition(rewritten)

    flow_code = (code or str(bundle["flow"]["code"])).strip()
    flow = bundle["flow"]
    flow_uid, created, version_uid = await _save_flow(
        session,
        code=flow_code,
        name=str(flow.get("name") or flow_code),
        description=flow.get("description") if isinstance(flow.get("description"), str) else None,
        entity=flow.get("entity") if isinstance(flow.get("entity"), str) else None,
        definition=definition,
    )
    return {
        "flow_uid": flow_uid,
        "created": created,
        "version_uid": version_uid,
        "forms": report["forms"],
        "templates": report["templates"],
        "warnings": warnings,
    }


async def _ensure_form(
    session: AsyncSession, code: str, spec: dict[str, Any] | None
) -> tuple[str | None, bool]:
    existing = (
        await session.execute(
            text(
                "SELECT uid FROM documents.forms WHERE code = :c AND tenant_id = auth.tenant_id()"
            ),
            {"c": code},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    if spec is None:
        return None, False
    uid = new_id()
    form_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO documents.forms (uid, tenant_id, code, name, description, status, "
                    "  entity_type, scoring, created_by) "
                    "VALUES (:u, auth.tenant_id(), :c, :n, :d, :s, :e, CAST(:sc AS jsonb), "
                    "  auth.uid()) RETURNING id"
                ),
                {
                    "u": uid,
                    "c": code,
                    "n": str(spec.get("name") or code),
                    "d": spec.get("description"),
                    "s": spec.get("status")
                    if spec.get("status") in ("draft", "published")
                    else "draft",
                    "e": spec.get("entity_type")
                    if spec.get("entity_type") in ("applicant", "account")
                    else "applicant",
                    "sc": json.dumps(spec["scoring"]) if spec.get("scoring") is not None else None,
                },
            )
        ).scalar_one()
    )
    for order, field in enumerate(spec.get("fields") or []):
        if not isinstance(field, dict) or not field.get("field_key"):
            continue
        await session.execute(
            text(
                "INSERT INTO documents.form_fields "
                "  (uid, tenant_id, form_id, field_key, field_type, label, section, description, "
                "   required, conditions, validation, options, sort_order) "
                "VALUES (:u, auth.tenant_id(), :f, :k, :ft, :l, :sec, :desc, :req, "
                "  CAST(:cond AS jsonb), CAST(:val AS jsonb), CAST(:opt AS jsonb), :o)"
            ),
            {
                "u": new_id(),
                "f": form_id,
                "k": str(field["field_key"]),
                "ft": str(field.get("field_type") or "text"),
                "l": str(field.get("label") or ""),
                "sec": field.get("section"),
                "desc": field.get("description"),
                "req": bool(field.get("required")),
                "cond": json.dumps(field["conditions"])
                if field.get("conditions") is not None
                else None,
                "val": json.dumps(field["validation"])
                if field.get("validation") is not None
                else None,
                "opt": json.dumps(field["options"]) if field.get("options") is not None else None,
                "o": order,
            },
        )
    return uid, True


async def _ensure_template(
    session: AsyncSession, code: str, spec: dict[str, Any] | None
) -> tuple[str | None, bool]:
    existing = (
        await session.execute(
            text(
                "SELECT uid FROM documents.templates "
                "WHERE code = :c AND tenant_id = auth.tenant_id()"
            ),
            {"c": code},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    if spec is None:
        return None, False
    uid = new_id()
    template_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, description, created_by) "
                    "VALUES (:u, auth.tenant_id(), :c, :n, :dt, :d, auth.uid()) RETURNING id"
                ),
                {
                    "u": uid,
                    "c": code,
                    "n": str(spec.get("name") or code),
                    "dt": str(spec.get("document_type") or "other"),
                    "d": spec.get("description"),
                },
            )
        ).scalar_one()
    )
    published = spec.get("status") == "published"
    version_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO documents.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                    "   metadata, created_by, published_by, published_at) "
                    "VALUES (:u, auth.tenant_id(), :t, 1, CAST(:st AS documents.template_status), "
                    "  CAST(:r AS documents.renderer), "
                    "  CAST(:schema AS jsonb), CAST(:meta AS jsonb), auth.uid(), "
                    "  CASE WHEN :pub THEN auth.uid() END, CASE WHEN :pub THEN now() END) "
                    "RETURNING id"
                ),
                {
                    "u": new_id(),
                    "t": template_id,
                    "st": "published" if published else "draft",
                    "r": str(spec.get("renderer") or "weasy"),
                    "schema": json.dumps(spec.get("schema") or {}),
                    "meta": json.dumps({"imported": True}),
                    "pub": published,
                },
            )
        ).scalar_one()
    )
    if published:
        await session.execute(
            text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :i"),
            {"v": version_id, "i": template_id},
        )
    return uid, True


async def _save_flow(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    entity: str | None,
    definition: dict[str, Any],
) -> tuple[str, bool, str]:
    """A new draft flow, or the next draft version of the flow that already holds this code —
    the same fork-or-update rule the editor's save uses, so an import never overwrites a
    published version."""
    existing = (
        (
            await session.execute(
                text(
                    "SELECT id, uid FROM flows.flows "
                    "WHERE code = :c AND tenant_id = auth.tenant_id()"
                ),
                {"c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    body = json.dumps(definition)
    if existing is None:
        flow_uid = new_id()
        flow_id = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO flows.flows (uid, tenant_id, code, name, description, "
                        "  entity, created_by) VALUES (:u, auth.tenant_id(), :c, :n, :d, :e, "
                        "  auth.uid()) RETURNING id"
                    ),
                    {"u": flow_uid, "c": code, "n": name, "d": description, "e": entity},
                )
            ).scalar_one()
        )
        version_uid = new_id()
        await session.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, definition, "
                "  created_by) VALUES (:u, auth.tenant_id(), :f, 1, CAST(:d AS jsonb), auth.uid())"
            ),
            {"u": version_uid, "f": flow_id, "d": body},
        )
        return flow_uid, True, version_uid
    flow_id = int(existing["id"])
    latest = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, version, status FROM flows.flow_versions "
                    "WHERE flow_id = :f ORDER BY version DESC LIMIT 1"
                ),
                {"f": flow_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if latest is not None and str(latest["status"]) == "draft":
        await session.execute(
            text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
            {"d": body, "i": latest["id"]},
        )
        return str(existing["uid"]), False, str(latest["uid"])
    version_uid = new_id()
    await session.execute(
        text(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, definition, "
            "  created_by) VALUES (:u, auth.tenant_id(), :f, :v, CAST(:d AS jsonb), auth.uid())"
        ),
        {
            "u": version_uid,
            "f": flow_id,
            "v": int(latest["version"]) + 1 if latest else 1,
            "d": body,
        },
    )
    return str(existing["uid"]), False, version_uid
