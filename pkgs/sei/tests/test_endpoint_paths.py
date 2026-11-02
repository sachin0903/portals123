"""Every path this package calls must exist in SEI's own merged spec.

This is the tripwire that matters most for a client written against ~200 vendor Swagger
files, and it earns its keep because of **how the mistake it catches fails**: a mistyped
service path is a 404 from an app gateway, which is indistinguishable from "SEI has not
provisioned that service for us" — the exact ambiguity that already cost real time on
``getAccountDetailsInfo`` (the per-firm API sheet spells it with a capital ``I``; SEI's own
Swagger, and therefore SEI, spells it ``getAccountDetailsinfo``). No mock can find that. Only
the spec can.

It also catches a path drifting under us when `spec/sei.openapi.json` is regenerated, which is
the other way this breaks: SEI renames a service, the spec updates, and nothing tells the
client until a live call.

The scan is static (AST over ``self.request(...)``) rather than a call-time assertion, so it
covers methods no test exercises — which is most of them, since the majority of this package
is a money instruction nobody fires in CI.

**The spec is usually right, and once it is not.** ``getAccountDetailsinfo`` is the sheet
being wrong. Account-template listing is the swagger file being wrong: concatenated
``servers.url`` is ``/enrollmentAccountTemplates`` (camelCase), i31's Apigee proxy is
``/enrollmentaccounttemplates`` (the all-lowercase convention the sibling client service
already follows in the same spec), and the camelCase path 404s ``Unable to identify proxy``.
``SWAGGER_PATHS_THAT_404`` is that inverse: calling the spec spelling FAILS, calling the
gateway spelling is rewritten onto the spec so a regen still proves the operation exists.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent
ENDPOINTS = PACKAGE / "src" / "sei" / "endpoints"
SPEC = PACKAGE / "spec" / "sei.openapi.json"

#: Spec-concatenated paths that 404 on the live gateway. Key = what `sei.openapi.json` has
#: (and what a naive "follow the spec" edit would restore); value = what Apigee actually
#: routes. Verified i31 22 Sep 2026: camelCase `Unable to identify proxy for host:
#: sei-default`; lowercase 200 with the Blackfinch AIM ISA / AIM IHT rows.
SWAGGER_PATHS_THAT_404: dict[str, str] = {
    "/enrollmentAccountTemplates/crm/account/templates": (
        "/enrollmentaccounttemplates/crm/account/templates"
    ),
    "/enrollmentAccountTemplates/crm/account/templates/trustForms": (
        "/enrollmentaccounttemplates/crm/account/templates/trustForms"
    ),
}
_LIVE_TO_SPEC = {live: spec for spec, live in SWAGGER_PATHS_THAT_404.items()}

#: A spec path template becomes this so a caller's f-string interpolation can match it.
_PARAM = re.compile(r"\{[^}/]+\}")


def _spec_matchers() -> list[tuple[str, re.Pattern[str], str]]:
    """(method, compiled path matcher, the spec's own template) for every operation."""
    document = json.loads(SPEC.read_text())
    out: list[tuple[str, re.Pattern[str], str]] = []
    for template, operations in document["paths"].items():
        pattern = re.compile(
            "^"
            + _PARAM.sub("[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}"))
            + "$"
        )
        for method in operations:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                out.append((method.upper(), pattern, template))
    return out


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments, so ``f"{SERVICE}/x"`` resolves."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def _resolve(node: ast.expr, constants: dict[str, str]) -> str | None:
    """The path a literal or f-string argument denotes, with interpolations as ``{}``.

    Returns ``None`` for anything genuinely dynamic — a bare variable — so the test skips it
    rather than inventing a path to check.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = _resolve(value.value, constants)
                parts.append(inner if inner is not None else "{param}")
            else:  # pragma: no cover - defensive
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, constants)
        right = _resolve(node.right, constants)
        return None if left is None or right is None else left + right
    return None


def _calls() -> list[tuple[str, str, str, int]]:
    """(module, METHOD, path, line) for every ``self.request(...)`` in the endpoint modules."""
    found: list[tuple[str, str, str, int]] = []
    for module in sorted(ENDPOINTS.glob("*.py")):
        tree = ast.parse(module.read_text())
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "request"):
                continue
            if len(node.args) < 2:
                continue
            method = _resolve(node.args[0], constants)
            path = _resolve(node.args[1], constants)
            if method is None or path is None:
                continue
            found.append((module.name, method.upper(), path, node.lineno))
    return found


def test_the_scan_finds_the_calls_it_is_meant_to_check() -> None:
    """Guard the guard: a refactor that changed the call shape would otherwise make this
    whole module pass by finding nothing."""
    calls = _calls()
    assert len(calls) >= 40, f"only found {len(calls)} request() calls — has the shape changed?"
    modules = {module for module, *_ in calls}
    assert "cash_receipts.py" in modules
    assert "enrollment.py" in modules


#: The merged spec's casing 404s; Apigee serves the lowercase proxy. The remainder of the
#: path is unchanged, so this is not a different operation.
_LIVE_CASE = {
    "/enrollmentaccounttemplates/crm/account/templates": (
        "/enrollmentAccountTemplates/crm/account/templates"
    ),
    "/enrollmentaccounttemplates/crm/account/templates/trustForms": (
        "/enrollmentAccountTemplates/crm/account/templates/trustForms"
    ),
}


@pytest.mark.parametrize(
    ("module", "method", "path", "line"),
    _calls(),
    ids=lambda value: str(value) if isinstance(value, str) else repr(value),
)
def test_every_called_path_exists_in_the_spec(
    module: str, method: str, path: str, line: int
) -> None:
    if path in SWAGGER_PATHS_THAT_404:
        pytest.fail(
            f"{module}:{line} calls {method} {path}, which 404s on i31 "
            f"(Unable to identify proxy for host: sei-default). "
            f"Use {SWAGGER_PATHS_THAT_404[path]} — the gateway spelling, same trap as "
            "getAccountDetailsinfo."
        )
    matchers = _spec_matchers()
    looked_up = _LIVE_CASE.get(path, path)
    candidates = [
        template
        for verb, pattern, template in matchers
        if verb == method and pattern.match(looked_up)
    ]
    if candidates:
        return
    # Named separately so the failure says which of the two mistakes was made: a path SEI
    # does not have at all, or one it has under a different verb.
    other_verbs = sorted({verb for verb, pattern, _ in matchers if pattern.match(looked_up)})
    if other_verbs:
        pytest.fail(
            f"{module}:{line} calls {method} {path}, but the spec has it only as "
            f"{', '.join(other_verbs)}"
        )
    pytest.fail(f"{module}:{line} calls {method} {path}, which is not in sei.openapi.json")


def test_swagger_paths_that_404_still_exist_in_the_spec() -> None:
    """If a regen drops the camelCase operation, this mapping is stale and the live
    spelling should be matched directly rather than rewritten onto a ghost."""
    templates = {template for _verb, _pattern, template in _spec_matchers()}
    missing = sorted(spec for spec in SWAGGER_PATHS_THAT_404 if spec not in templates)
    assert not missing, f"spec no longer has {missing}; drop them from SWAGGER_PATHS_THAT_404"


def test_account_template_calls_use_the_gateway_spelling() -> None:
    called = {path for _module, _method, path, _line in _calls()}
    assert "/enrollmentaccounttemplates/crm/account/templates" in called
    assert "/enrollmentaccounttemplates/crm/account/templates/trustForms" in called
    assert not any("enrollmentAccountTemplates" in path for path in called)
