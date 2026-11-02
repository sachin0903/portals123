"""Unit tests for the flow authoring's pure parts — definition validation, step visibility and
publish readiness (``app/flows/definitions.py`` + ``app/flows/visibility.py``). No DB.
"""

import pytest

from app.flows import definitions, visibility


# ---- definition validation ----
def test_valid_definition_normalises() -> None:
    out = definitions.validate_definition(
        {
            "steps": [
                {
                    "id": "details",
                    "type": "fill_form",
                    "title": "Your details",
                    "config": {"form_uid": "frm_1"},
                },
                {
                    "id": "terms",
                    "type": "accept_terms",
                    "title": "Terms",
                    "config": {"document_uid": "doc_1"},
                },
                {"id": "acct", "type": "setup_account", "title": "Account"},
            ],
            "milestones": [{"status": "submitted", "reached_when": ["details", "terms"]}],
        }
    )
    assert [s["id"] for s in out["steps"]] == ["details", "terms", "acct"]
    # default assignee comes from the step type (setup_account => staff)
    assert out["steps"][2]["assignee"] == "staff"
    assert out["steps"][0]["assignee"] == "client"
    assert out["steps"][0]["required"] is True


def test_unknown_step_type_rejected() -> None:
    with pytest.raises(definitions.DefinitionError):
        definitions.validate_definition({"steps": [{"id": "x", "type": "wat", "title": "X"}]})


def test_unknown_config_key_rejected() -> None:
    with pytest.raises(definitions.DefinitionError):
        definitions.validate_definition(
            {"steps": [{"id": "x", "type": "fill_form", "title": "X", "config": {"bogus": 1}}]}
        )


def test_duplicate_step_id_rejected() -> None:
    with pytest.raises(definitions.DefinitionError):
        definitions.validate_definition(
            {
                "steps": [
                    {"id": "x", "type": "fill_form", "title": "A"},
                    {"id": "x", "type": "accept_terms", "title": "B"},
                ]
            }
        )


def test_milestone_referencing_unknown_step_rejected() -> None:
    with pytest.raises(definitions.DefinitionError):
        definitions.validate_definition(
            {
                "steps": [{"id": "a", "type": "fill_form", "title": "A"}],
                "milestones": [{"status": "done", "reached_when": ["ghost"]}],
            }
        )


# ---- visibility ----
def test_visibility_single_condition_match() -> None:
    vis = {"field": "service_type", "operator": "equals", "value": "advised"}
    assert visibility.evaluate(vis, {"service_type": "advised"}) is True
    assert visibility.evaluate(vis, {"service_type": "execution_only"}) is False


def test_visibility_absent_is_visible() -> None:
    assert visibility.evaluate(None, {}) is True
    assert visibility.evaluate({}, {"service_type": "advised"}) is True


def test_visibility_group_and_or() -> None:
    conds = [
        {"field": "service_type", "operator": "equals", "value": "advised"},
        {"field": "type", "operator": "equals", "value": "individual"},
    ]
    ctx = {"service_type": "advised", "type": "organisation"}
    assert visibility.evaluate({"logic": "and", "conditions": conds}, ctx) is False
    assert visibility.evaluate({"logic": "or", "conditions": conds}, ctx) is True


def test_visibility_empty_operators() -> None:
    is_set = {"field": "email", "operator": "isNotEmpty"}
    assert visibility.evaluate(is_set, {"email": "a@b.com"}) is True
    assert visibility.evaluate(is_set, {"email": ""}) is False
    assert visibility.evaluate({"field": "email", "operator": "isEmpty"}, {}) is True


def test_visibility_unknown_operator_is_visible() -> None:
    assert visibility.evaluate({"field": "x", "operator": "wat", "value": 1}, {"x": 2}) is True


def test_visibility_contains_is_type_safe() -> None:
    # Regression (S4): `contains` with a non-string value against a string actual must NOT raise
    # (`5 in "advised"` → TypeError → 500 at instantiate). It returns False instead.
    assert (
        visibility.evaluate({"field": "s", "operator": "contains", "value": 5}, {"s": "advised"})
        is False
    )
    # String substring + list membership still behave.
    assert (
        visibility.evaluate(
            {"field": "s", "operator": "contains", "value": "vis"}, {"s": "advised"}
        )
        is True
    )
    assert (
        visibility.evaluate(
            {"field": "t", "operator": "contains", "value": "isa"}, {"t": ["isa", "gia"]}
        )
        is True
    )
    assert (
        visibility.evaluate({"field": "t", "operator": "notContains", "value": 5}, {"t": "advised"})
        is True
    )


# ---- publish readiness ----
def test_publish_issues_flags_missing_config() -> None:
    defn = {
        "steps": [
            {"id": "a", "type": "fill_form", "title": "A", "config": {}},
            # per_participant so this only trips the missing-config rule, not the no-actor rule
            # (see test_flows_publish_issues.py for that one).
            {
                "id": "b",
                "type": "accept_terms",
                "title": "B",
                "per_participant": True,
                "config": {},
            },
            {"id": "c", "type": "generate_document", "title": "C", "config": {}},
        ],
        "milestones": [],
    }
    issues = definitions.publish_issues(defn)
    assert len(issues) == 3
    assert any("form" in i for i in issues)
    assert any("declaration" in i or "PDF" in i for i in issues)
    assert any("template" in i for i in issues)


def test_publish_issues_empty_flow() -> None:
    assert definitions.publish_issues({"steps": [], "milestones": []}) == [
        "Add at least one step before publishing."
    ]


def test_publish_issues_ok_when_config_present() -> None:
    defn = {
        "steps": [
            {"id": "a", "type": "fill_form", "title": "A", "config": {"form_uid": "frm_1"}},
            {"id": "c", "type": "verify_identity", "title": "ID", "config": {}},
        ],
        "milestones": [],
    }
    assert definitions.publish_issues(defn) == []
