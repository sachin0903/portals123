"""Every declared filter must be answerable by the handler that uses it.

`crm.py` filters a listing with

    all(predicate(row, active[key]) for key, predicate in FILTERS.items())

so the registry drives the lookup and the handler's `active` dict has to supply every key. Add a
filter to the registry and forget the `active` entry — or add it to the WRONG handler's dict, three
hundred lines away in the same file, which is exactly what happened — and every read through that
handler raises `KeyError` before it returns a row.

It fails as a 500, which the portal reports as "couldn't be loaded" — indistinguishable from the
API being down or the firm having no data. Nothing in the type system or the linter can see it,
hence a test that reads the source and pairs the two up.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "app" / "routers" / "crm.py"
TREE = ast.parse(SRC.read_text(encoding="utf-8"))

# Registry name -> the handler that filters with it. Stated rather than inferred: the pairing is
# the thing under test, so deriving it from the code would make the test agree with itself.
REGISTRIES = {
    "_CONTACT_FILTERS": "list_contacts",
    "_TRANSACTION_FILTERS": "list_transactions",
    "_ACCOUNT_FILTERS": "list_accounts",
}


def _registry_keys(name: str) -> set[str]:
    """The keys of a module-level `NAME: listing.Filters = {...}` mapping."""
    for node in TREE.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name:
            assert isinstance(node.value, ast.Dict)
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{name} not found in {SRC.name}")


def _active_dict(node: ast.AST) -> ast.Dict | None:
    """The `active` dict literal a statement binds, or None if it binds something else.

    TWO NODE TYPES, because both forms appear in `crm.py`: `active = {...}` is an `Assign` with a
    list of targets, while `active: dict[...] = {...}` is an `AnnAssign` with a single one. A walk
    that knows only the first reports a handler as having NO `active` dict at all — which is a
    failure about this file, not about the pairing it exists to check.
    """
    if isinstance(node, ast.Assign):
        targets: tuple[ast.expr, ...] = tuple(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = (node.target,)
    else:
        return None
    if not any(getattr(t, "id", "") == "active" for t in targets):
        return None
    return node.value if isinstance(node.value, ast.Dict) else None


def _active_keys(handler: str) -> set[str]:
    """The keys of the `active` dict inside a handler."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == handler:
            for inner in ast.walk(node):
                found = _active_dict(inner)
                if found is not None:
                    return {k.value for k in found.keys if isinstance(k, ast.Constant)}
            raise AssertionError(f"{handler} has no `active = {{...}}` dict")
    raise AssertionError(f"{handler} not found in {SRC.name}")


def _params(handler: str) -> set[str]:
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == handler:
            args = node.args
            return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    raise AssertionError(f"{handler} not found in {SRC.name}")


@pytest.mark.parametrize(("registry", "handler"), sorted(REGISTRIES.items()))
def test_handler_answers_every_declared_filter(registry: str, handler: str) -> None:
    """`active` must cover the registry exactly — a missing key is a KeyError on every read."""
    declared = _registry_keys(registry)
    answered = _active_keys(handler)

    missing = declared - answered
    assert not missing, (
        f"{registry} declares {sorted(missing)} but {handler}'s `active` dict does not supply "
        f"it — every call raises KeyError before returning a row."
    )

    # And the other way: a key in `active` that no filter reads is dead weight at best, and at
    # worst a filter that was added to the wrong handler's dict and silently does nothing.
    stray = answered - declared
    assert not stray, (
        f"{handler}'s `active` dict supplies {sorted(stray)}, which {registry} never reads."
    )


@pytest.mark.parametrize(("registry", "handler"), sorted(REGISTRIES.items()))
def test_every_filter_is_reachable_as_a_query_parameter(registry: str, handler: str) -> None:
    """A filter nothing can set is a filter that does not exist. `date_from`/`date_to` are applied
    in SQL rather than through the registry, so they are not expected here."""
    for key in _registry_keys(registry):
        assert key in _params(handler), (
            f"{registry} declares '{key}' but {handler} takes no such parameter, so no caller "
            f"can ever set it."
        )


def test_every_paged_listing_ships_its_facet_counts() -> None:
    """`listing.page(facets=...)` with no `facet_counts=` ships `facet_counts: {}`, so the shared
    filter panel draws numbers on one page and none on the next. Four listings did that, and the
    tally is computed either way (`facets_for` throws it away)."""
    calls = [
        (src, node)
        for src in sorted((SRC.parent.parent).rglob("*.py"))
        for node in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "page"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "listing"
    ]
    assert calls, "no `listing.page(...)` calls found - the scan has stopped matching."
    for src, call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "facets" not in keywords or "facet_counts" in keywords, (
            f"{src.name}:{call.lineno} passes `facets=` but no `facet_counts=`."
        )
