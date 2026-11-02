"""Merge the two catalogues into one OpenAPI 3.1 document.

Neither source is sufficient alone, and they own different things:

    catalogue.json       ($metadata)  which actions EXIST, their exact parameter names, types
    docs-catalogue.json  (docs site)  which are REQUIRED, descriptions, option-set labels, examples

So the merge is not symmetric. **CSDL wins on names and types** — it is the org itself talking,
and where the docs disagree the docs are wrong (they document `cam_assetType` for a parameter
the org spells `cam_assettype`, which OData drops silently because parameter names are
case-sensitive). The docs win on everything CSDL structurally cannot express.

    uv run python scripts/build_openapi.py

Output: `spec/ml.openapi.json` — importable into Apidog/Postman/Insomnia, and the
input for any future generated client.

Three deliberate choices:

* Actions with no docs entry are still emitted, marked `x-undocumented`. Thirty-nine of the
  eighty-six are internal batch operations nobody has written up; leaving them out of the spec
  would misrepresent the API's surface as smaller than it is.
* The eight bound actions take a `{target}` path parameter rather than a guessed entity set.
  CSDL gives the binding entity *type* (`contact`), not the set name (`contacts`), and inventing
  plurals would produce paths that 404.
* The CTC illustration engine is included even though it is a different host and speaks XML,
  because it is one leg of the illustration flow and its content type is the thing that breaks
  first (`text/xml`, not `application/xml`).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = PACKAGE_ROOT / "catalogue.json"
DOCS_CATALOGUE = PACKAGE_ROOT / "docs-catalogue.json"
# Moved under `spec/` so the three vendor specs sit in one place
# (`pkgs/{morgan-lloyd,sei,seccl}/spec/`) and a reader looking for "the vendor spec"
# finds it in the same relative path for every provider.
OPENAPI = PACKAGE_ROOT / "spec" / "ml.openapi.json"

API_BASE = "https://cliftonassetdev.crm11.dynamics.com/api/data/v9.1"
CTC_BASE = "https://camtest.ctc.uk.com"

# CSDL primitives → JSON Schema. Dataverse only uses this handful for action parameters.
_PRIMITIVES: dict[str, dict[str, Any]] = {
    "String": {"type": "string"},
    "Int32": {"type": "integer", "format": "int32"},
    "Decimal": {"type": "number"},
    "Boolean": {"type": "boolean"},
    "DateTimeOffset": {"type": "string", "format": "date-time"},
    "Guid": {"type": "string", "format": "uuid"},
}
# The docs' own type words, for parameters CSDL declares as String but the docs describe as a
# nested JSON document passed as a string. Recorded, not enforced — the wire type is String.
_JSON_IN_STRING = re.compile(r"\bJSON\b")


def _schema_for(param: dict[str, Any], documented: dict[str, Any] | None) -> dict[str, Any]:
    schema = dict(_PRIMITIVES.get(param["type"], {"type": "object"}))
    if param["type"] not in _PRIMITIVES:
        # An entity-typed parameter (`contact`, `as_schemememberevent`): a Dataverse record.
        schema["description"] = f"Dataverse `{param['type']}` record"
    if param["collection"]:
        schema = {"type": "array", "items": schema}
    if not documented:
        return schema
    if documented.get("description"):
        schema["description"] = documented["description"]
    values = documented.get("enum_values") or list((documented.get("enum") or {}).values())
    if values:
        schema["enum"] = values
    if documented.get("enum"):
        # The labels are the whole reason to scrape: the wire value 809130016 means nothing on
        # its own. Kept as an extension so tooling can render a picker, not just a number.
        schema["x-enum-labels"] = documented["enum"]
    if documented.get("name_marker"):
        schema["x-docs-marker"] = documented["name_marker"]
    if documented.get("type") and _JSON_IN_STRING.search(documented["type"]):
        schema["x-json-in-string"] = True
        schema["contentMediaType"] = "application/json"
    return schema


def _body_example(http_block: str | None) -> Any:
    """The JSON body out of a documented HTTP request block, if it parses."""
    if not http_block:
        return None
    start = http_block.find("{")
    if start < 0:
        return None
    try:
        return json.loads(http_block[start:])
    except json.JSONDecodeError:
        return None


def _response_example(block: str | None) -> Any:
    if not block:
        return None
    try:
        return json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return None


def _describe(operation: dict[str, Any] | None, action: str) -> str:
    parts = [operation["summary"]] if operation and operation.get("summary") else []
    if operation and operation.get("notes"):
        parts.extend(operation["notes"])
    if not operation:
        parts.append(
            "Present in the org's $metadata but absent from the published documentation, so "
            "parameter names and types are known and their semantics are not."
        )
    parts.append(f"Unbound Dataverse action `{action}`.")
    return "\n\n".join(parts)


def action_operation(
    action: str, meta: dict[str, Any], documented: dict[str, Any] | None
) -> dict[str, Any]:
    fields = (documented or {}).get("fields", [])
    by_name = {field["name"]: field for field in fields}
    # Where the docs miscase a name, the name still comes from CSDL — but the description,
    # required flag and option-set labels plainly describe the same parameter, so match on the
    # folded name rather than throwing that away over the docs' typo. Exact match wins.
    folded = {field["name"].lower(): field for field in fields}
    matched = {
        p["name"]: by_name.get(p["name"]) or folded.get(p["name"].lower()) for p in meta["params"]
    }

    params = [p for p in meta["params"] if p["name"] != "entity"]
    properties = {p["name"]: _schema_for(p, matched[p["name"]]) for p in params}
    # Required comes from the docs alone: CSDL marks every action parameter Nullable="true".
    required = [p["name"] for p in params if (matched[p["name"]] or {}).get("required")]

    body: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        # Dataverse rejects unknown action parameters outright, so the schema is closed.
        "additionalProperties": False,
    }
    if required:
        body["required"] = required
    content: dict[str, Any] = {"schema": body}
    example = _body_example((documented or {}).get("examples", {}).get("request"))
    if example is not None:
        content["example"] = example

    response: dict[str, Any] = {"description": "Success"}
    returned = _response_example((documented or {}).get("examples", {}).get("response"))
    if returned is not None:
        response["content"] = {"application/json": {"example": returned}}
    elif meta["returns"]:
        response["content"] = {"application/json": {"schema": {"type": "object"}}}

    operation: dict[str, Any] = {
        "operationId": action,
        "summary": (documented or {}).get("title") or action,
        "description": _describe(documented, action),
        "tags": [(documented or {}).get("group") or "Undocumented"],
        "responses": {"200": response, "400": {"$ref": "#/components/responses/Error"}},
    }
    if properties:
        operation["requestBody"] = {
            "required": bool(required),
            "content": {"application/json": content},
        }
    if documented is None:
        operation["x-undocumented"] = True
    if meta["returns"]:
        operation["x-returns"] = meta["returns"]
    return operation


def bound_operation(action: str, meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A bound action, pathed through a caller-supplied target.

    CSDL names the binding entity *type*; the URL needs the entity *set*. Rather than guess the
    plural, the target segment is a path parameter — `contacts(<guid>)` — and the expected type
    is stated in the description.
    """
    entity = next((p["type"] for p in meta["params"] if p["name"] == "entity"), "crmbaseentity")
    operation = action_operation(action, meta, None)
    operation["description"] = (
        f"Bound Dataverse action `{action}`, invoked on a `{entity}` record. Supply `target` as "
        f"the entity set and key, e.g. `contacts(00000000-0000-0000-0000-000000000000)`."
    )
    operation["parameters"] = [
        {
            "name": "target",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": f"Entity set and key of the `{entity}` to invoke the action on.",
        }
    ]
    operation["x-bound-to"] = entity
    return f"/{{target}}/Microsoft.Dynamics.CRM.{action}", operation


def odata_operation(documented: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """A documented plain-OData read or patch against an entity set (not an action)."""
    method = documented["method"].lower()
    path = f"/{documented['entity_set']}"
    parameters = []
    if "(" in documented["url"].split("?")[0]:
        path += "({id})"
        parameters.append(
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
                "description": "Record GUID.",
            }
        )
    for query in documented.get("url_parameters", []):
        if query["name"].startswith("$"):
            parameters.append(
                {
                    "name": query["name"],
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": query.get("description"),
                }
            )
    operation: dict[str, Any] = {
        "operationId": f"{method}_{documented['anchor'].replace('-', '_')}",
        "summary": documented["title"],
        "description": "\n\n".join(
            filter(None, [documented.get("summary"), f"OData request: `{documented['url']}`"])
        ),
        "tags": [documented["group"]],
        "responses": {"200": {"description": "Success"}},
    }
    if parameters:
        operation["parameters"] = parameters
    fields = {
        f["name"]: _schema_for({"type": "String", "collection": False}, f)
        for f in documented["fields"]
    }
    if fields and method in {"post", "patch", "put"}:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object", "properties": fields}}},
        }
    returned = _response_example(documented.get("examples", {}).get("response"))
    if returned is not None:
        operation["responses"]["200"]["content"] = {"application/json": {"example": returned}}
    return path, method, operation


def ctc_illustration() -> dict[str, Any]:
    return {
        "operationId": "ctc_run_quote",
        "summary": "Run a CTC illustration (Bespoke XML v1.6)",
        "description": (
            "The illustration engine, a separate host from Dataverse. Takes CTC Bespoke XML "
            "v1.6 (CTC1) and answers XML. Unauthenticated: `nodb=true` plus an `<agent_name>` "
            "inside the document identifies the caller.\n\n"
            "The content type is `text/xml`, NOT `application/xml` — the engine answers an HTML "
            "error page to the latter, with HTTP 200, so a client that trusts the status code "
            "reports a successful illustration that does not exist."
        ),
        "tags": ["Illustrations"],
        "servers": [{"url": CTC_BASE, "description": "CTC staging (camtest); production is cam"}],
        "parameters": [
            {
                "name": "wci",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "const": "RunQuote"},
            },
            {
                "name": "nodb",
                "in": "query",
                "required": True,
                "schema": {"type": "boolean", "const": True},
                "description": "Credential-free mode; the caller is identified by <agent_name>.",
            },
        ],
        "requestBody": {
            "required": True,
            "content": {"text/xml": {"schema": {"type": "string", "format": "xml"}}},
        },
        "responses": {
            "200": {
                "description": (
                    "An XML `<message>` document on success. Any other root element — or an "
                    "HTML page — is an engine error despite the 200."
                ),
                "content": {"text/xml": {"schema": {"type": "string"}}},
            }
        },
    }


def build(catalogue: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any]:
    operations = docs["operations"]
    by_action = {op["action"]: op for op in operations if op["action"]}

    paths: dict[str, dict[str, Any]] = {}
    for action, meta in sorted(catalogue.items()):
        if meta["bound"]:
            path, operation = bound_operation(action, meta)
        else:
            path, operation = f"/{action}", action_operation(action, meta, by_action.get(action))
        paths.setdefault(path, {})["post"] = operation

    for documented in operations:
        if not documented.get("entity_set"):
            continue
        path, method, operation = odata_operation(documented)
        paths.setdefault(path, {})[method] = operation

    paths["/Pages/Remote.aspx"] = {"post": ctc_illustration()}

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Morgan Lloyd (Clifton Asset Management) API",
            "version": docs.get("version") or "0",
            "description": (
                "Generated, not hand-written: `scripts/introspect_api.py` reads the org's own "
                "`$metadata` for what exists and how it is spelled, "
                "`scripts/enrich_catalogue.py` scrapes the published docs for what is required "
                "and what the option-set numbers mean, and `scripts/build_openapi.py` merges "
                "them. Do not edit this file — regenerate it.\n\n"
                "`version` is the docs' own Change Log release, which is the only version "
                "number either source states."
            ),
        },
        "servers": [{"url": API_BASE, "description": "Development (cliftonassetdev)"}],
        "security": [{"bearerAuth": []}],
        # OpenAPI requires tag names to be unique; several operations share a group.
        "tags": [
            {"name": name} for name in sorted({op["group"] for op in operations if op.get("group")})
        ]
        + [{"name": "Undocumented", "description": "In $metadata only."}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "Azure AD app-only token for the Dataverse resource. Does not apply to "
                        "the CTC illustration host."
                    ),
                }
            },
            "responses": {
                "Error": {
                    "description": "Dataverse error",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "error": {
                                        "type": "object",
                                        "properties": {
                                            "code": {"type": "string"},
                                            "message": {"type": "string"},
                                        },
                                    }
                                },
                            }
                        }
                    },
                }
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge the catalogues into spec/ml.openapi.json.")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--docs", type=Path, default=DOCS_CATALOGUE)
    parser.add_argument("--out", type=Path, default=OPENAPI)
    args = parser.parse_args(argv)

    for source in (args.catalogue, args.docs):
        if not source.exists():
            print(f"missing {source.name} — run introspect_api.py and enrich_catalogue.py first")
            return 1

    catalogue = json.loads(args.catalogue.read_text())
    docs = json.loads(args.docs.read_text())
    document = build(catalogue, docs)
    args.out.write_text(json.dumps(document, indent=2) + "\n")

    inside = args.out.is_relative_to(PACKAGE_ROOT)
    where = args.out.relative_to(PACKAGE_ROOT) if inside else args.out
    operations = sum(len(methods) for methods in document["paths"].values())
    enriched = sum(
        1
        for path in document["paths"].values()
        for op in path.values()
        if not op.get("x-undocumented")
    )
    required = sum(
        len(
            op.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("required", [])
        )
        for path in document["paths"].values()
        for op in path.values()
    )
    print(f"wrote {where} — {operations} operations ({enriched} documented), {required} required")
    print(f"   info.version = {document['info']['version']} (docs Change Log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
