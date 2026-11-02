"""`lineage` has two states that look alike in SQL and mean opposite things here.

* **NULL** — this row's owning node is unknown, so it reaches nobody: `predicate.conformed`
  compiles to `len(lineage) = 0 OR list_has_any(lineage, [...])`, and both halves are NULL against
  a NULL list, so the row is excluded.
* **`[]`** — GLOBAL reference data, which "projects whole" in that function's own words. An ISIN is
  not owned by a firm and neither is a firm-wide fee register.

Every conformed model in the estate resolves its lineage from a parent (`platform.nodes`, or the
account/client that owns the row), and a LEFT JOIN miss is NULL. Wrapping that in
`coalesce(…, [])` — which twenty-one of them did, fourteen SEI and seven Seccl — turns "I could
not resolve this row's owner" into "this row belongs to everybody", so ONE unresolvable node
would publish an account, its positions, its transactions and its valuations into every enabled
tenant's slice. That is the exact
over-projection this module's docstring says the explicit table sets exist to prevent, arrived at
from the other direction.

The rule is asserted on the models' SQL rather than only on rows, because it is a rule about an
IDIOM: the failure is a new model copying the wrong one, and each provider's own suite would have
to execute 23 models with a fixture apiece to see it. It lives here rather than in the three
services because `predicate.conformed` is what gives `[]` its meaning — the sentinel is this
package's, so the guard is too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: The three provider projects, found relative to this package inside the monorepo. Skipped
#: outright when they are absent (an installed `book-sink` has no sibling services), because a
#: test that silently passes on an empty file list is the failure mode of every static scan.
SERVICES = Path(__file__).resolve().parents[3] / "services"

#: Relations whose rows belong to NO firm, so `[]` is the truth rather than a missing answer.
#: Keyed by provider, because it is a fact about that provider's data:
#:
#: * `instruments` — an ISIN is global; `0031_platform_conformed` leaves the table un-RLS'd for it.
#: * SEI `transfer_providers` — the Reference codes, `firm_id` null on every row.
#: * `fee_packages` / `transfer_providers` / `third_party_providers` — FIRM-WIDE registers. Scoping
#:   one to the firm ROOT looks tighter and is wrong, because containment tests ANCESTRY: a
#:   root-owned row matches only a tenant holding the root, so every tenant on a sub-node gets none
#:   of its own firm's pricing. That is how `third_party_providers` came to project 0 of 7 rows.
GLOBAL_RELATIONS: dict[str, frozenset[str]] = {
    "sei": frozenset({"instruments", "transfer_providers"}),
    "seccl": frozenset(
        {"instruments", "fee_packages", "transfer_providers", "third_party_providers"}
    ),
    "morgan-lloyd": frozenset(),
}

#: A parent's own lineage, taken as-is. The parent is the org of record for that axis, so a miss
#: there is genuinely unknown and arrives as NULL. `min`/`max` beside `any_value`: a grouped model
#: (SEI's valuations) picks one of the parent's rows by a total order rather than by whichever the
#: engine reached first, and none of the three can manufacture `[]` from a lineage the parent gave.
_PARENT_COLUMN = re.compile(r"^\s*(?:(?:any_value|min|max)\()?\w+\.lineage\)?\s*$")
#: A computed path, wrapped so that empty becomes NULL.
_NULLIF_WRAPPED = re.compile(r"nullif\(", re.I)
#: A literal list of the row's own keys — `[f.firm_id]`, `[src.ml_tenant_id]`. At least one element
#: whatever the values are, so it cannot collapse to the sentinel.
_KEY_LIST = re.compile(r"^\s*\[[^\[\]]+\]\s*$")


def _projection(sql: str) -> str | None:
    """The expression a model assigns to `lineage`, comments stripped.

    Two spellings: aliased (`… AS lineage,`), which may run over several lines, and a bare parent
    column needing no alias (`o.lineage,`). For the aliased form, read backwards until the
    parentheses AND any CASE/END balance — crude, and enough to tell the shapes apart. CASE has to
    count: `third_party_providers` ends its projection on a bare `END`, which balances on its own
    line and would otherwise hide the whole expression from the scan.
    """
    body = re.sub(r"^\s*--.*$", "", sql, flags=re.M)

    bare = re.search(r"^\s*(\w+\.lineage),\s*$", body, re.M)
    if bare:
        return bare.group(1)

    aliased = re.search(r"^.*?\s+AS lineage,\s*$", body, re.M | re.I)
    if not aliased:
        return None
    collected: list[str] = []
    depth = 0
    for line in reversed(body[: aliased.end()].splitlines()):
        collected.insert(0, line)
        depth += line.count(")") - line.count("(")
        depth += len(re.findall(r"\bEND\b", line, re.I)) - len(re.findall(r"\bCASE\b", line, re.I))
        if depth <= 0 and collected[0].strip():
            break
    return re.sub(r"\s+AS lineage,\s*$", "", " ".join(collected), flags=re.I)


def _models() -> list[tuple[str, Path]]:
    return [
        (provider, path)
        for provider in sorted(GLOBAL_RELATIONS)
        for path in sorted((SERVICES / provider / "models" / "platform").glob("*.sql"))
    ]


MODELS = _models()
pytestmark = pytest.mark.skipif(
    not MODELS, reason="the provider projects are not on disk (installed package, not the monorepo)"
)


def test_the_scan_sees_every_provider_and_more_than_one_shape() -> None:
    """A regex that matches nothing passes every assertion below."""
    found: dict[str, list[str]] = {}
    for provider, path in MODELS:
        if _projection(path.read_text()) is not None:
            found.setdefault(provider, []).append(path.stem)
    assert set(found) == set(GLOBAL_RELATIONS), found
    assert sum(len(names) for names in found.values()) >= 25, found


@pytest.mark.parametrize(
    ("provider", "path"), MODELS, ids=[f"{provider}:{path.stem}" for provider, path in MODELS]
)
def test_only_a_global_relation_publishes_an_empty_lineage(provider: str, path: Path) -> None:
    projection = _projection(path.read_text())
    if projection is None:
        return
    relation = path.stem
    if relation in GLOBAL_RELATIONS[provider]:
        assert "[]" in projection, (
            f"{provider}'s platform.{relation} is declared global here, so its lineage should say "
            f"so: {projection.strip()}"
        )
        return
    accepted = (
        _PARENT_COLUMN.match(projection)
        or _NULLIF_WRAPPED.search(projection)
        or _KEY_LIST.match(projection)
    )
    assert accepted, (
        f"{provider}'s platform.{relation} can publish `[]`, which `predicate.conformed` reads as "
        f"GLOBAL — the row would project into every enabled tenant's slice. Take the parent's "
        f"lineage as-is, or wrap a computed one in `nullif(…, []::VARCHAR[])`, or add the relation "
        f"to GLOBAL_RELATIONS if its rows really do belong to every firm. Got: "
        f"{projection.strip()}"
    )
