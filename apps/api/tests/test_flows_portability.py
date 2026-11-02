"""Flow export and import (`flows/portability.py`, `GET /flows/{uid}/export`, `POST /flows/import`).

The pure half — which uids a definition names, rewriting them, what to warn about — runs without
a database. The round trip (export from one firm, import into another, the forms created and the
definition rewritten to the receiving firm's uids) needs Docker.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.flows import definitions, portability
from app.permissions import FLOWS_READ, FLOWS_WRITE
from app.routers import flows as api


def _definition(form_uid: str = "frm_src", template_uid: str = "tpl_src") -> dict[str, Any]:
    return {
        "version": 3,
        "intake": {"subject": "person", "review": False},
        "milestones": [],
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "title": "Start",
                "config": {},
                "position": None,
                "depends_on": [],
                "next": "ask",
            },
            {
                "id": "ask",
                "kind": "step",
                "type": "fill_form",
                "title": "Ask",
                "scope": "participant",
                "assignee": "client",
                "config": {"form_uid": form_uid, "mode": "gather", "title": "Ask"},
                "next": "letter",
            },
            {
                "id": "letter",
                "kind": "step",
                "type": "generate_document",
                "title": "Letter",
                "scope": "application",
                "assignee": "staff",
                "config": {"template_uid": template_uid, "title": "Letter"},
                "next": None,
            },
        ],
    }


# --- pure ---------------------------------------------------------------------------------------


def test_referenced_uids_finds_forms_and_templates_in_nodes_and_steps() -> None:
    definition = _definition()
    definition["steps"] = [{"id": "ask", "config": {"form_uid": "frm_src"}}]
    refs = portability.referenced_uids(definition)
    assert refs["form"] == {"frm_src"}
    assert refs["template"] == {"tpl_src"}
    assert refs["term"] == set()


def test_rewrite_refs_replaces_known_uids_everywhere_and_leaves_the_original_alone() -> None:
    definition = _definition()
    definition["steps"] = [{"id": "ask", "config": {"form_uid": "frm_src"}}]
    out = portability.rewrite_refs(
        definition, {"form": {"frm_src": "frm_dst"}, "template": {"tpl_src": "tpl_dst"}}
    )
    assert out["nodes"][1]["config"]["form_uid"] == "frm_dst"
    assert out["nodes"][2]["config"]["template_uid"] == "tpl_dst"
    assert out["steps"][0]["config"]["form_uid"] == "frm_dst"
    assert definition["nodes"][1]["config"]["form_uid"] == "frm_src"


def test_warnings_name_what_does_not_travel() -> None:
    definition = _definition()
    definition["nodes"].append(
        {
            "id": "open",
            "kind": "step",
            "type": "setup_account",
            "title": "Open the account",
            "config": {"product_id": 4, "model_id": 9},
            "tasks": [{"title": "Check it", "assign_to": {"kind": "user", "user_id": "u1"}}],
        }
    )
    warnings = portability.portability_warnings(definition, {"form": {"frm_src"}})
    assert any("“Ask” names a form that was not in the file" in w for w in warnings)
    assert any("product_id, model_id" in w for w in warnings)
    assert any("“Check it” is assigned to a user" in w for w in warnings)
    assert not any("Letter" in w for w in warnings)


def test_each_warning_is_said_once_although_a_graph_carries_every_step_twice() -> None:
    """A validated definition has the step in `nodes` AND in the derived `steps`; the import sheet
    keys its list by the text, so a repeat is a rendering error, not emphasis."""
    definition = definitions.validate_definition(_definition())
    assert any(step["id"] == "letter" for step in definition["steps"])
    warnings = portability.portability_warnings(definition, {"template": {"tpl_src"}})
    assert warnings == ["“Letter” names a template that was not in the file. Choose it again."]


def test_read_bundle_refuses_what_it_cannot_read() -> None:
    with pytest.raises(portability.BundleError):
        portability.read_bundle({"format": "something.else", "version": 1})
    with pytest.raises(portability.BundleError, match="version 9"):
        portability.read_bundle({"format": portability.FORMAT, "version": 9})
    with pytest.raises(portability.BundleError, match="name its flow"):
        portability.read_bundle({"format": portability.FORMAT, "version": 1, "flow": {}})
    with pytest.raises(portability.BundleError, match="no flow definition"):
        portability.read_bundle({"format": portability.FORMAT, "version": 1, "flow": {"code": "x"}})


# --- the round trip -----------------------------------------------------------------------------


def _principal(*perms: Any) -> Principal:
    return Principal(
        user_id="wos_staff",
        organisation_id="org_1",
        role="adviser",
        permissions=[p.slug if hasattr(p, "slug") else str(p) for p in perms],
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _firm(sess: AsyncSession, label: str) -> tuple[int, str]:
    """A tenant with one login; returns ``(tenant_id, user_uuid)``."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"{label}_{s}",
            a=f"{label}-{s}",
            n=f"{label} {s}",
        )
    )
    user = await _scalar(
        sess,
        "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
        u=f"usr_{s}",
        t=tenant,
        e=f"{label}_{s}@t.test",
    )
    return tenant, str(user)


async def _act_as(sess: AsyncSession, tenant: int, user: str) -> None:
    """Publish the claims ``auth.tenant_id()`` and ``auth.uid()`` read, the way tenant_session
    does. service_role still bypasses RLS; the writes just need to know whose they are."""
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"tenant_id": tenant, "user_uuid": user, "role": "authenticated"})},
    )


async def _source_flow(sess: AsyncSession, tenant: int, user: str) -> str:
    """A published flow in the exporting firm, with the form and template it names."""
    s = uuid.uuid4().hex[:8]
    form_uid = f"frm_{s}"
    form_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.forms "
            "  (uid, tenant_id, code, name, status, entity_type, scoring) "
            "VALUES (:u, :t, 'port_ask', 'Ask', 'published', 'applicant', "
            "  CAST(:sc AS jsonb)) RETURNING id",
            u=form_uid,
            t=tenant,
            sc=json.dumps({"fields": [], "bands": []}),
        )
    )
    for order, (key, ftype) in enumerate((("first_name", "text"), ("dob", "dateOfBirth"))):
        await sess.execute(
            text(
                "INSERT INTO documents.form_fields (uid, tenant_id, form_id, field_key, "
                "  field_type, label, section, required, validation, sort_order) "
                "VALUES (:u, :t, :f, :k, :ft, :l, 'About you', true, "
                "  CAST(:v AS jsonb), :o)"
            ),
            {
                "u": f"fld_{s}_{order}",
                "t": tenant,
                "f": form_id,
                "k": key,
                "ft": ftype,
                "l": key.replace("_", " ").title(),
                "v": json.dumps({"width": 6}),
                "o": order,
            },
        )
    template_uid = f"tpl_{s}"
    template_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.templates (uid, tenant_id, code, name, document_type, "
            "  created_by) VALUES (:u, :t, 'port_letter', 'Letter', 'suitability', "
            "  CAST(:by AS uuid)) RETURNING id",
            u=template_uid,
            t=tenant,
            by=user,
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.template_versions (uid, tenant_id, template_id, "
            "  version_number, status, schema, created_by, published_by, published_at) "
            "VALUES (:u, :t, :tpl, 1, 'published', CAST(:s AS jsonb), CAST(:by AS uuid), "
            "  CAST(:by AS uuid), now()) RETURNING id",
            u=f"tv_{s}",
            t=tenant,
            tpl=template_id,
            s=json.dumps({"html": "<p>Dear {{ client.name }}</p>", "css": ""}),
            by=user,
        )
    )
    await sess.execute(
        text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :i"),
        {"v": version_id, "i": template_id},
    )
    flow_uid = f"flw_{s}"
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, description, status) "
            "VALUES (:u, :t, 'port_journey', 'Portable journey', 'Travels well.', 'published') "
            "RETURNING id",
            u=flow_uid,
            t=tenant,
        )
    )
    definition = api.definitions.validate_definition(_definition(form_uid, template_uid))
    # An older draft sits above the published version, so the export has to pick by status
    # rather than by "latest".
    published_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition, published_at) VALUES (:u, :t, :f, 1, 'published', "
            "  CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{s}_1",
            t=tenant,
            f=flow_id,
            d=json.dumps(definition),
        )
    )
    await sess.execute(
        text(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition) VALUES (:u, :t, :f, 2, 'draft', '{\"steps\": []}'::jsonb)"
        ),
        {"u": f"flv_{s}_2", "t": tenant, "f": flow_id},
    )
    await sess.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :i"),
        {"v": published_id, "i": flow_id},
    )
    return flow_uid


@pytest.mark.asyncio
async def test_export_carries_the_published_definition_with_its_forms_and_templates(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant, user = await _firm(sess, "exp")
    await _act_as(sess, tenant, user)
    flow_uid = await _source_flow(sess, tenant, user)

    bundle = await api.export_flow(flow_uid, _principal(FLOWS_READ), sess)
    assert bundle["format"] == portability.FORMAT
    assert bundle["version"] == portability.FORMAT_VERSION
    assert bundle["flow"] == {
        "code": "port_journey",
        "name": "Portable journey",
        "description": "Travels well.",
        "entity": None,
    }
    # The published version, not the later draft.
    assert bundle["source"] == {"version": 1, "status": "published"}
    steps = {s["id"]: s for s in bundle["definition"]["steps"]}
    form_uid = steps["ask"]["config"]["form_uid"]
    template_uid = steps["letter"]["config"]["template_uid"]
    assert bundle["refs"]["forms"] == {form_uid: "port_ask"}
    assert bundle["refs"]["templates"] == {template_uid: "port_letter"}
    [form] = bundle["forms"]
    assert form["code"] == "port_ask"
    assert form["status"] == "published"
    assert form["scoring"] == {"fields": [], "bands": []}
    assert [f["field_key"] for f in form["fields"]] == ["first_name", "dob"]
    assert form["fields"][0]["validation"] == {"width": 6}
    [template] = bundle["templates"]
    assert template["code"] == "port_letter"
    assert template["status"] == "published"
    assert template["schema"]["html"].startswith("<p>Dear")


@pytest.mark.asyncio
async def test_export_of_an_unknown_flow_is_404(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.export_flow("flw_nope", _principal(FLOWS_READ), service_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_creates_the_missing_form_and_template_and_a_draft_flow(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    source, source_user = await _firm(sess, "src")
    await _act_as(sess, source, source_user)
    bundle = await api.export_flow(
        await _source_flow(sess, source, source_user), _principal(FLOWS_READ), sess
    )

    target, target_user = await _firm(sess, "dst")
    await _act_as(sess, target, target_user)
    out = await api.import_flow(api.ImportFlow(bundle=bundle), _principal(FLOWS_WRITE), sess)
    assert out.created is True
    assert out.flow.code == "port_journey"
    assert out.flow.status == "draft"
    assert out.flow.published_version is None
    assert out.forms.created == ["port_ask"] and out.forms.reused == []
    assert out.templates.created == ["port_letter"] and out.templates.reused == []
    assert out.warnings == []

    # The form and template now exist in the RECEIVING firm, by code, with their content.
    new_form = (
        (
            await sess.execute(
                text(
                    "SELECT uid, status, scoring, "
                    "  (SELECT count(*) FROM documents.form_fields ff WHERE ff.form_id = f.id) "
                    "    AS fields "
                    "FROM documents.forms f WHERE tenant_id = :t AND code = 'port_ask'"
                ),
                {"t": target},
            )
        )
        .mappings()
        .one()
    )
    assert new_form["status"] == "published"
    assert new_form["fields"] == 2
    assert new_form["scoring"] == {"fields": [], "bands": []}
    new_template = (
        (
            await sess.execute(
                text(
                    "SELECT t.uid, v.status, v.schema, t.created_by FROM documents.templates t "
                    "JOIN documents.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.tenant_id = :t AND t.code = 'port_letter'"
                ),
                {"t": target},
            )
        )
        .mappings()
        .one()
    )
    assert new_template["status"] == "published"
    assert str(new_template["created_by"]) == target_user
    # And the draft definition names THOSE, not the exporting firm's.
    definition = await _scalar(
        sess,
        "SELECT definition FROM flows.flow_versions WHERE uid = :u",
        u=out.version_uid,
    )
    if isinstance(definition, str):
        definition = json.loads(definition)
    steps = {s["id"]: s for s in definition["steps"]}
    assert steps["ask"]["config"]["form_uid"] == new_form["uid"]
    assert steps["letter"]["config"]["template_uid"] == new_template["uid"]
    assert steps["ask"]["config"]["form_uid"] != next(iter(bundle["refs"]["forms"]))


@pytest.mark.asyncio
async def test_import_reuses_a_form_the_firm_already_has_and_forks_a_draft_of_an_existing_flow(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    source, source_user = await _firm(sess, "src2")
    await _act_as(sess, source, source_user)
    bundle = await api.export_flow(
        await _source_flow(sess, source, source_user), _principal(FLOWS_READ), sess
    )

    target, target_user = await _firm(sess, "dst2")
    await _act_as(sess, target, target_user)
    # The receiving firm already has a form and a template with those codes, and a published
    # flow with that code: theirs are kept, and the import must not touch the live version.
    own_flow = await _source_flow(sess, target, target_user)
    own_form = await _scalar(
        sess,
        "SELECT uid FROM documents.forms WHERE tenant_id = :t AND code = 'port_ask'",
        t=target,
    )
    await sess.execute(
        text("UPDATE documents.forms SET name = 'Our own ask' WHERE uid = :u"), {"u": own_form}
    )

    out = await api.import_flow(api.ImportFlow(bundle=bundle), _principal(FLOWS_WRITE), sess)
    assert out.created is False
    assert out.flow.uid == own_flow
    assert out.flow.published_version == 1
    assert out.forms.reused == ["port_ask"] and out.forms.created == []
    assert out.templates.reused == ["port_letter"]
    assert (
        await _scalar(sess, "SELECT name FROM documents.forms WHERE uid = :u", u=own_form)
        == "Our own ask"
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.forms WHERE tenant_id = :t AND code = 'port_ask'",
            t=target,
        )
        == 1
    )
    definition = await _scalar(
        sess, "SELECT definition FROM flows.flow_versions WHERE uid = :u", u=out.version_uid
    )
    if isinstance(definition, str):
        definition = json.loads(definition)
    assert {s["id"]: s for s in definition["steps"]}["ask"]["config"]["form_uid"] == own_form
    # The existing draft (v2) was updated in place; the published v1 is untouched.
    assert (
        await _scalar(
            sess,
            "SELECT status FROM flows.flow_versions WHERE uid = :u",
            u=out.version_uid,
        )
        == "draft"
    )
    assert out.flow.version_count == 2


@pytest.mark.asyncio
async def test_import_under_another_code_and_with_a_form_the_file_lacks(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    target, target_user = await _firm(sess, "dst3")
    await _act_as(sess, target, target_user)
    bundle = {
        "format": portability.FORMAT,
        "version": 1,
        "flow": {"code": "port_journey", "name": "Portable journey"},
        "definition": _definition(),
        "refs": {"forms": {"frm_src": "port_ask"}, "templates": {}},
        "forms": [],
        "templates": [],
    }
    out = await api.import_flow(
        api.ImportFlow(bundle=bundle, code="port_copy"), _principal(FLOWS_WRITE), sess
    )
    assert out.flow.code == "port_copy"
    assert out.forms.created == [] and out.forms.reused == []
    assert any("“Ask” names a form that was not in the file" in w for w in out.warnings)
    assert any("“Letter” names a template that was not in the file" in w for w in out.warnings)


@pytest.mark.asyncio
async def test_import_refuses_a_file_that_is_not_a_flow(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.import_flow(
            api.ImportFlow(bundle={"hello": "world"}), _principal(FLOWS_WRITE), service_session
        )
    assert exc.value.status_code == 422
    assert "not a flow export" in str(exc.value.detail)
