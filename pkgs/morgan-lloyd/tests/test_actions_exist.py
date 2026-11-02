"""Every Dynamics action and entity path this client names must exist in the org.

`spec/ml.openapi.json` is generated from the org's own ``$metadata`` (what exists and how it
is spelled) enriched with the published docs (what is required), so it is the authority on
whether an action is real. That makes this the tripwire for the mistake this package has
actually made twice:

* ``cam_updateschemevaluation`` — a placeholder action name that shipped, was wired into a
  scheduled sink, and was pinned by a mocked test that could not tell the difference.
* ``cam_createbeneficiary`` — the name the docs use; the org only has the plural
  ``cam_createbeneficiaries``.

A mock cannot catch either. Dynamics answers an unknown unbound action with a generic failure,
so the symptom is a vendor error rather than a 404, and it looks like a permissions or payload
problem. Checking the names against ``$metadata`` is the only cheap way to know.

`docs/morgan-lloyd/endpoint-coverage.md` §1 records eleven operations whose documented name is
not the action the client posts, and the CLIENT is authoritative there — those names are
verified against the live org, which is exactly why they are in the spec and pass here.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent
ENDPOINTS = PACKAGE / "src" / "morgan_lloyd" / "endpoints"
SPEC = PACKAGE / "spec" / "ml.openapi.json"

_PARAM = re.compile(r"\{[^}/]+\}")


def _spec_paths() -> set[str]:
    """Every path in the spec, plus the bare action name for the unbound ``/cam_x`` form."""
    document = json.loads(SPEC.read_text())
    names: set[str] = set()
    for template in document["paths"]:
        names.add(template)
        names.add(template.lstrip("/"))
        names.add(_PARAM.sub("{}", template).lstrip("/"))
    return names


def _string_args(method_name: str) -> list[tuple[str, str, int]]:
    """(module, first string argument, line) for every ``self.<method_name>(...)`` call."""
    found: list[tuple[str, str, int]] = []
    for module in sorted(ENDPOINTS.glob("*.py")):
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == method_name):
                continue
            # `self.call_action(...)`, not `body.get(...)` — a dict's own `.get` has the same
            # attribute name and its first argument is a JSON key, not a path.
            if not (isinstance(func.value, ast.Name) and func.value.id == "self"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((module.name, first.value, node.lineno))
            elif isinstance(first, ast.JoinedStr):
                parts = [
                    value.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    else "{}"
                    for value in first.values
                ]
                found.append((module.name, "".join(parts), node.lineno))
    return found


def test_the_scan_finds_the_calls_it_is_meant_to_check() -> None:
    """Guard the guard: if the call shape changed, everything below would pass vacuously."""
    actions = _string_args("call_action")
    assert len(actions) >= 25, f"only found {len(actions)} call_action() calls"
    assert any(name == "cam_createpolicyvaluation" for _, name, _ in actions)


@pytest.mark.parametrize(("module", "action", "line"), _string_args("call_action"))
def test_every_action_exists_in_the_org(module: str, action: str, line: int) -> None:
    paths = _spec_paths()
    if action in paths or f"/{action}" in paths:
        return
    # A bound action reached through the {target} form.
    if any(path.endswith(f"Microsoft.Dynamics.CRM.{action}") for path in paths):
        return
    pytest.fail(
        f"{module}:{line} posts action `{action}`, which is not in the org's $metadata "
        f"(spec/ml.openapi.json). Either the name is wrong or the spec needs regenerating."
    )


def test_entity_reads_are_not_checked_here_and_why() -> None:
    """Deliberately NOT asserting that every ``get``/``patch_entity`` path names a real
    collection.

    `ml.openapi.json` documents the 93 paths the vendor publishes; the org exposes **2,556**
    entity sets, and `GET /api/data/v9.1/$metadata` (31 MB) is the only complete list of them.
    So the spec cannot distinguish "not a real collection" from "real but undocumented", and
    the eight entity reads this client makes (`contacts`, `transfers`, `accounts`,
    `pensionContributions`, `contributionApplications`, `cam_nominationofbeneficiaries`,
    `cam_personalpolicies`, `cam_schemes`) are all in the second group — verified live, per
    `docs/morgan-lloyd/endpoint-coverage.md`.

    Asserting against the spec here would fail on correct code, which is worse than not
    asserting: the check would be deleted the first time someone hit it, and the action check
    above — which the spec CAN answer, because it is generated from ``$metadata`` — would go
    with it.
    """
    assert SPEC.exists()
