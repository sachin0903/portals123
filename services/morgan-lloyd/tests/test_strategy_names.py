"""Every declared strategy is one interlace will actually accept.

`plan` does not check this. A strategy name is resolved at APPLY time
(`resolve_strategy(materialise, strategy, …)`), so an invalid one plans clean, shows every model as
`rebuild`, and fails on the first real run — which is exactly what happened: interlace 2.0.1 renamed
the strategies with NO aliases (`replace` was `full`, `merge` was `merge_by_key`, `scd` was
`scd_type_2`), the 2.0.0 upgrade flipped the materialise plane and left 100 declarations on the old
names across three services, and every gate stayed green.

So this asks interlace's own resolver, rather than keeping a list of valid names that would need
updating on the next rename. It is deliberately in each service's suite and not shared: a service's
model declarations are its own, and a shared test would be one more place to remember.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from interlace.exceptions import PlanError
from interlace.strategies import resolve_strategy

MODELS = Path(__file__).resolve().parent.parent / "models"

#: `strategy: <name>` in SQL front-matter, `strategy="<name>"` in a Python decorator. The type
#: annotation `strategy: str = "full_merge"` is deliberately not matched — it declares nothing.
_SQL = re.compile(r"^\s*strategy:\s*([a-z_]+)\s*$", re.M)
_PY = re.compile(r'strategy="([a-z_]+)"')
_MATERIALISE_SQL = re.compile(r"^\s*materialise:\s*([a-z_]+)\s*$", re.M)
_MATERIALISE_PY = re.compile(r'materialise="([a-z_]+)"')


def _declarations() -> list[tuple[str, str, str]]:
    """(file, materialise, strategy) for every model declaration in this project."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(MODELS.rglob("*")):
        if path.suffix not in (".sql", ".py") or not path.is_file():
            continue
        text = path.read_text()
        name = str(path.relative_to(MODELS))
        if path.suffix == ".sql":
            materialise = (_MATERIALISE_SQL.search(text) or [None, "virtual"])[1]
            found += [(name, materialise, s) for s in _SQL.findall(text)]
        else:
            # A Python module can hold several models; each may set its own materialise, and the
            # default when it does not is `virtual`.
            declared = _MATERIALISE_PY.findall(text) or ["virtual"]
            found += [(name, declared[0], s) for s in _PY.findall(text)]
    return found


def test_there_are_declarations_to_check() -> None:
    """A regex that stopped matching would make every assertion below vacuously true. The floor is
    low because the smallest of the three projects declares twelve; it is here to catch a broken
    pattern, not to police how many models a provider has."""
    assert len(_declarations()) >= 5


@pytest.mark.parametrize(
    "declaration", _declarations(), ids=lambda d: f"{d[0]}:{d[2]}" if isinstance(d, tuple) else ""
)
def test_the_strategy_resolves(declaration: tuple[str, str, str]) -> None:
    name, materialise, strategy = declaration
    try:
        # A key/time_column is supplied so a keyed strategy is not rejected for the wrong reason:
        # what is under test is whether the NAME exists on this plane, not whether this model
        # declares its key (interlace's own loader checks that).
        resolve_strategy(materialise, strategy, ("external_id",), "as_of_date")
    except PlanError as error:
        pytest.fail(
            f"{name} declares strategy '{strategy}' on the '{materialise}' plane, which interlace "
            f"refuses: {error}. 2.0.1 renamed replace (was full), merge (was merge_by_key) and "
            f"scd (was scd_type_2), with no aliases."
        )


# What 2.0.1 renamed, with no aliases. Named explicitly rather than derived, because a future
# resolver that happened to accept one again would make the test above pass while the models were
# still on a name this project decided against.
#
# `incremental_by_time` was listed here and is NOT retired: interlace 2.2.0 accepts it and refuses
# `incremental`, so the entry was backwards in both directions — it would have failed a model on
# the valid name while the failure message sent the reader to the invalid one. Nothing declared
# either, so it was latent; `test_every_retired_name_is_really_refused` is what stops the list
# going stale again.
RETIRED = {"full", "merge_by_key", "scd_type_2"}


def test_the_old_names_are_gone() -> None:
    offenders = [(name, s) for name, _, s in _declarations() if s in RETIRED]
    assert not offenders, f"pre-2.0.1 strategy names still declared: {offenders}"


def test_every_retired_name_is_really_refused() -> None:
    """The list above is a claim about interlace, so it is checked against interlace.

    A name that this set calls retired but the resolver still accepts is a rule we are enforcing
    for no reason; a name it calls retired that the resolver never knew is a typo that silently
    guards nothing. Either way the set is wrong, and the only way to find out was a real run.
    """
    for name in RETIRED:
        with pytest.raises(PlanError):
            resolve_strategy("table", name, ("external_id",), "as_of_date")
