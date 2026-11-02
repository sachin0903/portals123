"""The catalogue generators: CSDL parsing, docs scraping, and the merge between them.

Every case here is a shape the real sources actually produce and an earlier version of the
parsers got wrong — the docs are inconsistent enough that these are regression tests, not
hypotheticals. The fixtures are trimmed copies of live markup, not invented markup.

The scripts live in `scripts/` rather than `src/` (they are dev tooling and must not ship in the
installed package), so they are loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


introspect = _load("introspect_api")
enrich = _load("enrich_catalogue")
build = _load("build_openapi")


# --------------------------------------------------------------------------- $metadata


METADATA = (
    '<Schema Namespace="Microsoft.Dynamics.CRM">'
    '<Action Name="cam_createscheme">'
    '<Parameter Name="contactId" Type="Edm.String" Nullable="true" />'
    '<Parameter Name="schemeType" Type="Edm.Int32" Nullable="true" />'
    '<ReturnType Type="mscrm.cam_createschemeResponse" Nullable="true" /></Action>'
    '<Action Name="cam_GetContactSharePointFolder" IsBound="true">'
    '<Parameter Name="entity" Type="mscrm.contact" Nullable="false" />'
    '<Parameter Name="Names" Type="Collection(Edm.String)" Nullable="true" /></Action>'
    '<Action Name="NotOurs"><Parameter Name="x" Type="Edm.String" /></Action>'
    "</Schema>"
)


def test_parse_actions_reads_names_types_and_boundness() -> None:
    catalogue = introspect.parse_actions(METADATA)

    # Only `cam_*` is ours; the org exposes hundreds of Microsoft-owned actions too.
    assert set(catalogue) == {"cam_createscheme", "cam_GetContactSharePointFolder"}
    assert catalogue["cam_createscheme"]["params"] == [
        {"name": "contactId", "type": "String", "collection": False},
        {"name": "schemeType", "type": "Int32", "collection": False},
    ]
    assert catalogue["cam_createscheme"]["returns"] == "cam_createschemeResponse"
    assert catalogue["cam_createscheme"]["bound"] is False


def test_parse_actions_unwraps_collections_and_marks_bound_actions() -> None:
    bound = introspect.parse_actions(METADATA)["cam_GetContactSharePointFolder"]

    assert bound["bound"] is True
    assert bound["params"][1] == {"name": "Names", "type": "String", "collection": True}
    assert bound["returns"] is None


# --------------------------------------------------------------------------- the docs site


def _page(body: str) -> str:
    return f'<div class="content">{body}</div>'


def _operation(body: str) -> dict[str, Any]:
    operations = enrich.parse_docs(_page(f"<h1 id='g'>Schemes</h1>{body}"))
    assert len(operations) == 1
    return operations[0]


def test_action_is_read_from_the_request_block_not_the_heading() -> None:
    # The section titled "Create Investment" posts to cam_createwrapper, and there is no
    # cam_createinvestment in the org at all. Deriving the action from the title invents one.
    operation = _operation(
        "<h2 id='create-investment'>Create Investment</h2>"
        "<h3 id='http-request'>HTTP Request</h3>"
        "<p><code>POST https://host/api/data/v9.1/cam_createwrapper</code></p>"
    )

    assert operation["action"] == "cam_createwrapper"
    assert operation["entity_set"] is None


@pytest.mark.parametrize(
    ("method", "url", "action", "entity_set"),
    [
        ("POST", "https://h/v9.1/cam_createscheme", "cam_createscheme", None),
        # An OData read or patch against a collection is a table, not an operation.
        (
            "GET",
            "https://h/v9.1/cam_nominationofbeneficiaries",
            None,
            "cam_nominationofbeneficiaries",
        ),
        ("PATCH", "https://h/v9.1/cam_contributions(GUID)", None, "cam_contributions"),
        ("GET", "https://h/v9.1/cam_schemes?$filter=x eq 'y'", None, "cam_schemes"),
        ("GET", "https://h/v9.1/contacts?$select=firstname", None, None),
    ],
)
def test_target_separates_actions_from_entity_sets(
    method: str, url: str, action: str | None, entity_set: str | None
) -> None:
    assert enrich._target(method, url) == (action, entity_set)


def _fields_table(rows: str) -> str:
    return (
        "<h2 id='op'>Op</h2><h3 id='http-request'>HTTP Request</h3>"
        "<p><code>POST https://h/v9.1/cam_op</code></p>"
        "<h3 id='fields'>Fields</h3><table><thead><tr><th>Field</th><th>Type</th>"
        f"<th>Required</th><th>Description</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _row(name: str, required: str, description: str) -> str:
    return f"<tr><td>{name}</td><td>int</td><td>{required}</td><td>{description}</td></tr>"


def test_required_flag_keeps_the_condition_when_it_is_qualified() -> None:
    operation = _operation(
        _fields_table(
            _row("plain", "Yes", "d")
            + _row("optional", "No", "d")
            # "No*" points at a footnote: required only when creating a joint liability. The
            # bare boolean loses that, so the original wording is kept alongside it.
            + _row("conditional", "No*", "d")
        )
    )
    by_name = {field["name"]: field for field in operation["fields"]}

    assert by_name["plain"]["required"] is True
    assert by_name["optional"]["required"] is False
    assert "required_note" not in by_name["optional"]
    assert by_name["conditional"]["required"] is False
    assert by_name["conditional"]["required_note"] == "No*"


def test_field_name_markers_are_stripped_but_recorded() -> None:
    # "Fields marked with ** will not update if 0 or null is provided" — the marker is real
    # behaviour, but a name carrying it does not match the parameter the org accepts.
    operation = _operation(_fields_table(_row("cam_balance**", "No", "d")))

    assert operation["fields"][0]["name"] == "cam_balance"
    assert operation["fields"][0]["name_marker"] == "**"


@pytest.mark.parametrize(
    ("description", "enum", "enum_values"),
    [
        # The colon sits inside the <strong> about half the time.
        (
            "Allowed Values (<br><strong>Mr</strong>: 1, <br><strong>Ms</strong>: 2)",
            {"Mr": 1, "Ms": 2},
            None,
        ),
        ("Possible Values: (<br><strong>Male:</strong> 1)", {"Male": 1}, None),
        # A label with its own parentheses — a naive [^)]* clause stops inside it.
        (
            "Allowed Values ( <strong>Own (Mortgaged):</strong> 10, <strong>Renting:</strong> 11)",
            {"Own (Mortgaged)": 10, "Renting": 11},
            None,
        ),
        # "Values" doubled by the docs.
        ("Possible Values: Values ( <strong>Mortgage</strong>: 5)", {"Mortgage": 5}, None),
        # Partly labelled: 16 is "ML SIPP", 17 is anonymous. Dropping 17 narrows the API.
        ("Allowed Values ( <strong>ML SIPP</strong>: 16, 17)", {"ML SIPP": 16}, [16, 17]),
        # Wholly unlabelled — sippType lists bare integers, and the trailing prose has numbers
        # of its own that must not be swept in.
        (
            "Allowed Values (809130003, 809130016) For more information contact ML on 2 lines",
            None,
            [809130003, 809130016],
        ),
        # String-valued option sets.
        (
            'Possible values are: "cam_policy", and "cam_scheme" (matching the type returned)',
            None,
            ["cam_policy", "cam_scheme"],
        ),
        ("Possible values: ( systemuser , team )", None, ["systemuser", "team"]),
        ("A plain description with the number 42 in it", None, None),
    ],
)
def test_option_sets_are_extracted_from_prose(
    description: str, enum: dict[str, int] | None, enum_values: list[Any] | None
) -> None:
    field = enrich._field({"field": "f", "type": "int", "description": description})

    assert field.get("enum") == enum
    assert field.get("enum_values") == enum_values


def test_url_parameters_are_kept_out_of_the_body() -> None:
    # A path GUID in the body's required list made the action look like it was missing a
    # parameter it never takes.
    operation = _operation(
        "<h2 id='op'>Op</h2><h3 id='http-request'>HTTP Request</h3>"
        "<p><code>POST https://h/v9.1/cam_op</code></p>"
        "<h3 id='url-parameters'>URL Parameters</h3><table><thead><tr><th>Parameter</th>"
        "<th>Description</th></tr></thead><tbody><tr><td>GUID</td><td>The record</td></tr>"
        "</tbody></table>"
        "<h3 id='fields'>Fields</h3><table><thead><tr><th>Field</th><th>Type</th>"
        "<th>Required</th><th>Description</th></tr></thead><tbody>"
        "<tr><td>cam_amount</td><td>decimal</td><td>Yes</td><td>d</td></tr></tbody></table>"
    )

    assert [field["name"] for field in operation["fields"]] == ["cam_amount"]
    assert [field["name"] for field in operation["url_parameters"]] == ["GUID"]


def test_slates_example_pane_boilerplate_is_not_a_summary() -> None:
    # Almost every section opens with a blockquote saying "The above command returns HTTP
    # structured like this:". Taken as the summary, all 65 operations describe themselves
    # identically and say nothing.
    operation = _operation(
        "<h2 id='op'>Op</h2>"
        "<blockquote><p>The above command returns HTTP structured like this:</p></blockquote>"
        "<h3 id='http-request'>HTTP Request</h3><p><code>POST https://h/v9.1/cam_op</code></p>"
    )

    assert operation["summary"] is None


def test_a_real_summary_before_the_example_is_kept() -> None:
    operation = _operation(
        "<h2 id='op'>Op</h2><p>This endpoint creates a Financial Adviser Contact.</p>"
        "<blockquote><p>The above command returns HTTP structured like this:</p></blockquote>"
        "<h3 id='http-request'>HTTP Request</h3><p><code>POST https://h/v9.1/cam_op</code></p>"
    )

    assert operation["summary"] == "This endpoint creates a Financial Adviser Contact."


def test_field_table_footnotes_are_carried_as_notes() -> None:
    operation = _operation(
        _fields_table(_row("jointLiabilities", "No*", "d"))
        + "<p>*Required if creating a Joint Liability.</p>"
    )

    assert "*Required if creating a Joint Liability." in operation["notes"]


def test_docs_version_comes_from_the_newest_change_log_release() -> None:
    page = "<h1 id='change-log'>Change Log</h1><h2 id='release-1-57'>Release 1.57</h2>"
    page += "<h2 id='release-1-56'>Release 1.56</h2>"

    assert enrich.docs_version(page) == "1.57"


def test_change_log_and_auth_sections_are_not_operations() -> None:
    page = _page(
        "<h1 id='change-log'>Change Log</h1><h2 id='release-1-57'>Release 1.57</h2>"
        "<h1 id='authentication'>Authentication</h1><h2 id='token'>Aquire an Access Token</h2>"
        "<h1 id='schemes'>Schemes</h1><h2 id='op'>Create Scheme</h2>"
        "<h3 id='http-request'>HTTP Request</h3><p><code>POST https://h/v9.1/cam_op</code></p>"
    )

    assert [op["title"] for op in enrich.parse_docs(page)] == ["Create Scheme"]


# --------------------------------------------------------------------------- the merge


def _catalogue() -> dict[str, Any]:
    return {
        "cam_createscheme": {
            "params": [
                {"name": "contactId", "type": "String", "collection": False},
                {"name": "schemeType", "type": "Int32", "collection": False},
                {"name": "cam_assettype", "type": "Int32", "collection": False},
            ],
            "returns": "cam_createschemeResponse",
            "bound": False,
        },
        "cam_GetDeferredDate": {
            "params": [{"name": "entity", "type": "contact", "collection": False}],
            "returns": None,
            "bound": True,
        },
    }


def _docs() -> dict[str, Any]:
    return {
        "source": "https://dev.api.morgan-lloyd.co.uk/",
        "version": "1.57",
        "operations": [
            {
                "group": "Schemes",
                "title": "Create Scheme",
                "anchor": "create-scheme",
                "summary": None,
                "method": "POST",
                "url": "https://h/v9.1/cam_createscheme",
                "action": "cam_createscheme",
                "entity_set": None,
                "fields": [
                    {
                        "name": "contactId",
                        "type": "string",
                        "required": True,
                        "description": "The contact",
                    },
                    {
                        "name": "schemeType",
                        "type": "int",
                        "required": True,
                        "description": "Allowed Values ( ML SIPP: 16, 17)",
                        "enum": {"ML SIPP": 16},
                        "enum_values": [16, 17],
                    },
                    # The docs' casing is wrong; OData parameter names are case-sensitive.
                    {
                        "name": "cam_assetType",
                        "type": "int",
                        "required": True,
                        "description": "Type",
                    },
                ],
                "url_parameters": [],
                "headers": [],
                "response_fields": [],
                "tables": {},
                "notes": [],
                "examples": {
                    "request": (
                        'POST /cam_createscheme HTTP/1.1\nHost: h\n\n{\n "contactId": "abc"\n}\n'
                    ),
                    "response": '{"schemeId": "def"}',
                },
            }
        ],
    }


def test_merge_takes_parameter_names_from_metadata_not_the_docs() -> None:
    document = build.build(_catalogue(), _docs())
    schema = document["paths"]["/cam_createscheme"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]

    # The org spells it `cam_assettype`. Emitting the docs' `cam_assetType` would produce a
    # payload Dataverse silently drops the parameter from.
    assert "cam_assettype" in schema["properties"]
    assert "cam_assetType" not in schema["properties"]
    # The docs entry still describes this parameter, so its semantics are kept: the typo costs
    # the spelling, not the description or the required flag.
    assert schema["properties"]["cam_assettype"]["description"] == "Type"
    assert "cam_assettype" in schema["required"]


def test_merge_takes_required_and_option_set_labels_from_the_docs() -> None:
    document = build.build(_catalogue(), _docs())
    schema = document["paths"]["/cam_createscheme"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]

    # CSDL marks every action parameter Nullable="true", so `required` can only come from prose.
    assert schema["required"] == ["contactId", "schemeType", "cam_assettype"]
    assert schema["properties"]["schemeType"]["enum"] == [16, 17]
    assert schema["properties"]["schemeType"]["x-enum-labels"] == {"ML SIPP": 16}
    assert schema["properties"]["contactId"]["description"] == "The contact"
    assert schema["additionalProperties"] is False


def test_merge_carries_the_documented_examples() -> None:
    operation = build.build(_catalogue(), _docs())["paths"]["/cam_createscheme"]["post"]
    content = operation["requestBody"]["content"]["application/json"]

    assert content["example"] == {"contactId": "abc"}
    assert operation["responses"]["200"]["content"]["application/json"]["example"] == {
        "schemeId": "def"
    }


def test_undocumented_actions_are_still_emitted_and_flagged() -> None:
    catalogue = _catalogue()
    catalogue["cam_sendemail"] = {
        "params": [{"name": "to", "type": "String", "collection": False}],
        "returns": None,
        "bound": False,
    }
    operation = build.build(catalogue, _docs())["paths"]["/cam_sendemail"]["post"]

    assert operation["x-undocumented"] is True
    assert operation["tags"] == ["Undocumented"]
    # Nothing is known to be required, so nothing is claimed to be.
    assert "required" not in operation["requestBody"]["content"]["application/json"]["schema"]


def test_bound_actions_take_a_target_path_parameter() -> None:
    document = build.build(_catalogue(), _docs())
    path = "/{target}/Microsoft.Dynamics.CRM.cam_GetDeferredDate"
    operation = document["paths"][path]["post"]

    # CSDL gives the entity TYPE (`contact`); the URL needs the entity SET (`contacts`). The
    # plural is not derivable, so the caller supplies the segment.
    assert operation["x-bound-to"] == "contact"
    assert [p["name"] for p in operation["parameters"]] == ["target"]
    # The bound receiver is the URL, never a body key.
    assert "requestBody" not in operation


def test_documented_entity_sets_become_odata_paths() -> None:
    docs = _docs()
    docs["operations"].append(
        {
            "group": "Beneficiaries",
            "title": "Update Beneficiary",
            "anchor": "update-beneficiary",
            "summary": None,
            "method": "PATCH",
            "url": "https://h/v9.1/cam_nominationofbeneficiaries(GUID)",
            "action": None,
            "entity_set": "cam_nominationofbeneficiaries",
            "fields": [{"name": "cam_percentageoffund", "type": "decimal", "required": True}],
            "url_parameters": [],
            "headers": [],
            "response_fields": [],
            "tables": {},
            "notes": [],
            "examples": {},
        }
    )
    paths = build.build(_catalogue(), docs)["paths"]

    assert "patch" in paths["/cam_nominationofbeneficiaries({id})"]
    parameters = paths["/cam_nominationofbeneficiaries({id})"]["patch"]["parameters"]
    assert [p["name"] for p in parameters] == ["id"]


def test_spec_version_is_the_docs_release() -> None:
    assert build.build(_catalogue(), _docs())["info"]["version"] == "1.57"


def test_tag_names_are_unique() -> None:
    docs = _docs()
    docs["operations"].append({**docs["operations"][0], "title": "Update Scheme"})
    tags = [tag["name"] for tag in build.build(_catalogue(), docs)["tags"]]

    # OpenAPI rejects a duplicated tag name, and most groups hold several operations.
    assert len(tags) == len(set(tags))


def test_checked_in_openapi_matches_its_sources() -> None:
    """`spec/ml.openapi.json` is derived, so a stale copy is a lie about the API.

    The two inputs need credentials and network to refresh, so CI cannot regenerate them — but
    the merge is pure, so CI can prove the checked-in spec is what those inputs produce. Run
    `moon run morgan-lloyd:build-spec` when this fails.
    """
    package = Path(__file__).resolve().parent.parent
    catalogue = json.loads((package / "catalogue.json").read_text())
    docs = json.loads((package / "docs-catalogue.json").read_text())
    committed = json.loads((package / "spec" / "ml.openapi.json").read_text())

    assert build.build(catalogue, docs) == committed
