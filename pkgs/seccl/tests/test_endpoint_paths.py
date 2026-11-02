"""Every path the endpoint mixins build must appear in Seccl's own collection.

Seccl publishes no OpenAPI document, so the Postman collection IS the contract — and it is a
usable one for this purpose: 377 requests, each with a method and a URL template. Checking
against it catches the mistake this API's shape invites, which is **its two URL conventions**:
a create POSTs to the bare collection with the firm in the BODY (``POST /client``), while every
read and update puts the firm in the PATH (``GET /client/{firm}/{id}``).

Getting that backwards fails in a way local tests cannot see. ``POST /client/{firm}`` is a 404
that reads as a routing problem; ``GET /client`` without the firm segment is a differently
shaped refusal. Neither says "you used the wrong convention".

The scan resolves `_paths.collection/scoped/record` calls statically, so it covers the methods
no test exercises — which is most of the writes, since they are money instructions nobody fires
in CI.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent
ENDPOINTS = PACKAGE / "src" / "seccl" / "endpoints"
COLLECTION = PACKAGE / "spec" / "seccl.postman_collection.json"

#: `{{apiRoute}}` / `{{firmId}}` / `{{clientId}}` … — every Postman variable.
_VARIABLE = re.compile(r"\{\{[^}]+\}\}")
#: The two route variables that stand for the base URL rather than a path segment.
_ROUTE_VARIABLES = ("{{apiRoute}}", "{{pfolioApiRoute}}", "{{authRoute}}")


def _collection_paths() -> set[tuple[str, str]]:
    """(METHOD, normalised path) for every documented request.

    Normalised means: base-URL variable stripped, query string dropped, every remaining
    ``{{var}}`` and every opaque id segment reduced to ``*``. Seccl's collection hard-codes
    real ids in places (``/account/{{firmId}}/025F416``), so a variable-only normalisation
    would miss those.
    """
    document = json.loads(COLLECTION.read_text())
    out: set[tuple[str, str]] = set()

    def visit(items: list[dict]) -> None:
        for item in items:
            if "item" in item:
                visit(item["item"])
                continue
            request = item.get("request") or {}
            url = request.get("url")
            raw = url.get("raw", "") if isinstance(url, dict) else (url or "")
            method = (request.get("method") or "").upper()
            if not raw or not method:
                continue
            for route in _ROUTE_VARIABLES:
                raw = raw.replace(route, "")
            if raw.startswith("http"):  # the S3 upload in the manual-transfer flow
                continue
            out.add((method, _normalise(raw.split("?", 1)[0])))

    visit(document["item"])
    return out


def _normalise(path: str) -> str:
    """Collapse variable and opaque-id segments to ``*`` so two spellings of the same route
    compare equal."""
    segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if _VARIABLE.search(segment) or segment in {"{}", "{param}"}:
            segments.append("*")
        # An opaque Seccl id: short, alphanumeric, at least one digit. `v1`, `list`, `summary`
        # and the resource names all fail one of those tests.
        elif (
            len(segment) <= 12
            and segment.isalnum()
            and any(c.isdigit() for c in segment)
            and (segment != "v1")
        ):
            segments.append("*")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def _built_paths() -> list[tuple[str, str, str, int]]:
    """(module, METHOD, normalised path, line) for every path the mixins build."""
    found: list[tuple[str, str, str, int]] = []
    for module in sorted(ENDPOINTS.glob("*.py")):
        if module.name.startswith("_"):
            continue
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            method = _method_of(func.attr, node)
            if method is None:
                continue
            path = _path_of(node)
            if path is None:
                continue
            found.append((module.name, method, _normalise(path), node.lineno))
    return found


def _method_of(attribute: str, node: ast.Call) -> str | None:
    """The HTTP verb a client call implies, or None if this is not a client call."""
    if attribute == "write":
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value.upper()
        return None
    if attribute in {"get", "get_record", "get_records", "get_list"}:
        # `self.get_records(...)`, not `dict.get(...)`.
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "self":
                return "GET"
        return None
    return None


def _path_of(node: ast.Call) -> str | None:
    """The path argument, resolved through the `_paths` helpers."""
    index = 1 if (isinstance(node.func, ast.Attribute) and node.func.attr == "write") else 0
    if len(node.args) <= index:
        return None
    return _resolve(node.args[index])


def _resolve(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _resolve(node.left), _resolve(node.right)
        if left is None:
            return None
        return left if right is None else left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        helper = node.func.attr
        if helper in {"collection", "scoped", "record"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                extra = {"collection": 0, "scoped": 1, "record": 2}[helper]
                return "/" + first.value + "".join("/{}" for _ in range(extra))
    return None


def test_the_scan_finds_the_calls_it_is_meant_to_check() -> None:
    """Guard the guard: a changed call shape would make everything below pass vacuously."""
    calls = _built_paths()
    assert len(calls) >= 35, f"only found {len(calls)} client calls — has the shape changed?"
    assert ("clients.py", "POST", "/client", 43) in calls or any(
        path == "/client" and method == "POST" for _, method, path, _ in calls
    )


@pytest.mark.parametrize(("module", "method", "path", "line"), _built_paths())
def test_every_built_path_is_documented(module: str, method: str, path: str, line: int) -> None:
    documented = _collection_paths()
    if (method, path) in documented:
        return
    # Seccl accepts a trailing slash on several list routes and the collection is inconsistent
    # about including it; the API is not, so both spellings count as the same route.
    if (method, path.rstrip("/")) in documented or (method, path + "/") in documented:
        return
    others = sorted({verb for verb, documented_path in documented if documented_path == path})
    if others:
        pytest.fail(
            f"{module}:{line} calls {method} {path}, but the collection documents it only as "
            f"{', '.join(others)} — the two URL conventions are easy to swap (see the module "
            f"docstring)"
        )
    pytest.fail(
        f"{module}:{line} calls {method} {path}, which is not in spec/seccl.postman_collection.json"
    )
