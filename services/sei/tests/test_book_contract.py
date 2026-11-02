"""What SEI's conformed layer must publish before the book pipeline can read it.

`pkgs/book-sink` projects one shape per entity for every provider — that is the whole point of
docs/platform-model.md §3 — so a column the sink reads and SEI does not publish is a DuckDB Binder
Error that aborts the tenant, and the ledger records it as "unavailable": indistinguishable from
"this firm has no data with this provider". Exactly the failure that hid SEI's transactions
publishing `account_number` where the materialiser reads `account_external_id`.

So this is a STANDING GAP TABLE, in the sense docs/contributions.md uses the term: the difference is
data, machine-checked, and it fails in BOTH directions. A gap that closes fails here too, which is
the prompt to wire that entity up.

Read with sqlglot rather than by executing the models: SEI's raw layer is 47 reports whose column
lists come from the staged files' own headers, so a stub warehouse for the whole layer would need a
corpus this repo deliberately does not carry. Parsing gives the published column names exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import sqlglot
from book_sink import CONFORMED, required_columns
from book_sink.entities import Entity

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: The sink's entity source relation -> SEI's model, where the names differ. EMPTY, now that
#: `assets` has been renamed to the canonical §3 `instruments` (the same rename Morgan Lloyd's
#: `contacts` -> `clients` was). Kept as the seam rather than deleted: SEI's raw layer still calls
#: the report Assets, because that is SEI's own name for it, and a future divergence belongs here
#: rather than in a silent mismatch.
SOURCE_ALIASES: dict[str, str] = {}

#: Everything SEI's conformed layer does not publish that the sink reads. EMPTY — and it is empty
#: because both remaining columns were answered from a real drop rather than decided:
#:
#: * `product_key` is `ACCOUNT_TYPE`, not `PORTFOLIO_TYPE`. `ACCT_TYPE` in the Reference report
#:   decodes 157 Investment Account / 172 Stocks and Shares ISA / 173 Stocks and Shares Junior ISA /
#:   176 House Account, while `PORTFOLIO_TYPE` is 189 Capital Portfolio / 190 Income Portfolio — an
#:   investment-purpose split with no bearing on the tax wrapper. From the column names alone the
#:   two were indistinguishable, which is exactly why this waited for data.
#: * `status` maps `ACCT_STATUS` (1 Pre-Open, 3 Open, 5 Pre-Close, 6 Closed, 8/9/10 Work In
#:   Progress) onto draft | active | closed.
#:
#: The table stays, and stays checked in both directions: a gap that WIDENS is a conformed model
#: that stopped publishing something, which would otherwise surface only as a tenant's slice going
#: quietly missing.
KNOWN_GAPS: dict[str, set[str]] = {
    "nodes": set(),
    "instruments": set(),
    # The Reference report's ceding counterparties. Empty because the conformed model
    # publishes every column the sink reads, with Origo/ATG/rails as typed empties —
    # the file does not carry them.
    "transfer_providers": set(),
    "clients": set(),
    "accounts": set(),
    # The container below an account (0119). Empty from the first run rather than after a round of
    # corrections, because the sink's projection and the conformed model were written against each
    # other — which is what this table is for.
    "portfolios": set(),
    "valuations": set(),
    "transactions": set(),
    # The firm's own staff (0123), merged into `auth.users` through `auth.merge_provider_user`
    # rather than the generic policy. Empty for the same reason `portfolios` is: the sink's
    # projection and the conformed model were written against each other.
    "users": set(),
    # The model catalogue, discovered from the accounts that name a model, because SEI's own
    # `ModelsIntExt` and `ModelAlloc` are declared and 0 rows on all 74 drops. Empty because the
    # conformed model publishes every column the FOUR `models`-sourced entities ask of a PROVIDER —
    # including the ones SEI says nothing about, as typed NULLs, which is what stops a Binder Error
    # aborting the whole tenant's projection. (`entry`/`idx`/`body` are bound by the siblings' own
    # FROM clauses and are excluded by `_derived` rather than listed here.)
    "models": set(),
    # The firm's PERIODIC fee packages (feePackageProfileId), conformed from FeePackageRule. Empty
    # because the model publishes every column the fee_packages sink projection reads; one-time and
    # real-time fee types are separate axes, deliberately not in this relation.
    "fee_packages": set(),
}


def _published(model: str) -> set[str]:
    sql = re.sub(r"/\*.*?\*/", "", (MODELS / f"{model}.sql").read_text(), count=1, flags=re.S)
    return set(sqlglot.parse_one(sql.strip(), dialect="duckdb").named_selects)


#: Names an entity's own `from_sql` BINDS, which are therefore not asked of the provider.
#: `_MODEL_LINES_FROM` unnests `allocations` into `entry` and `range(len(…))` into `idx`;
#: `_MODEL_COMMENTARY_FROM` resolves `commentary`'s three possible shapes into `body`.
_DERIVED_ALIAS = re.compile(r"\bAS\s+([a-z_][a-z0-9_]*)", re.I)


def _derived(entity: Entity) -> set[str]:
    """Columns the entity's FROM clause defines for itself.

    `required_columns` reads `s.<col>` off the projection TEXT, so it cannot tell a column the
    PROVIDER must publish from one the sink's own subquery just bound. Counting the second kind as
    a gap would report every provider three columns short of a contract none of them can meet —
    and, worse, would hold SEI unregistered against a shortfall that does not exist.
    """
    return {m.group(1).lower() for m in _DERIVED_ALIAS.finditer(entity.from_sql)}


def _needs() -> list[tuple[str, frozenset[str]]]:
    """Per SEI model, the UNION of what every sink entity reading that relation needs FROM SEI.

    Union, not the first entity's: two entities can read one relation (the firm's shelf and each
    platform's reference for it both come from `accounts`), and taking one of them would understate
    the contract — which is how this test first reported a smaller gap than the real one.

    Minus what each entity's own FROM binds — see `_derived`.
    """
    needs: dict[str, set[str]] = {}
    for entity in CONFORMED:
        model = SOURCE_ALIASES.get(entity.source, entity.source)
        if (MODELS / f"{model}.sql").exists():
            needs.setdefault(model, set()).update(required_columns(entity) - _derived(entity))
    return [(model, frozenset(columns)) for model, columns in sorted(needs.items())]


@pytest.mark.parametrize("model,needed", _needs(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_gap_is_exactly_what_we_think_it_is(model: str, needed: frozenset[str]) -> None:
    """Fails in both directions on purpose. A gap that has closed is a green light to wire the
    entity up — and a gap that has WIDENED is a conformed model that stopped publishing something,
    which would otherwise only surface as a tenant's slice going quietly missing."""
    missing = needed - _published(model)
    expected = KNOWN_GAPS.get(model)
    assert expected is not None, f"{model} has no entry in KNOWN_GAPS — add one with its reason"
    assert missing == expected, (
        f"platform.{model}: the sink reads {sorted(missing)} that SEI does not publish; "
        f"KNOWN_GAPS says {sorted(expected)}. "
        f"{'Closed — wire it up and shrink the table.' if missing < expected else ''}"
        f"{'WIDENED — a model stopped publishing something.' if missing > expected else ''}"
    )


def test_sei_is_not_registered_while_a_gap_remains() -> None:
    """A partial slice is worse than none: an account with no status and no owner reads as a real
    account nobody can act on. So `book_sink` deliberately registers no entities for SEI until the
    table above is empty, and this is the check that keeps the two honest."""
    from book_sink.entities import PROVIDER_ENTITIES

    outstanding = {model for model, gap in KNOWN_GAPS.items() if gap}
    if outstanding:
        assert PROVIDER_ENTITIES.get("sei") == (), (
            f"SEI is registered but has gaps in {sorted(outstanding)} — a partial slice writes a "
            f"partial book"
        )
    else:
        assert PROVIDER_ENTITIES.get("sei"), "every gap is closed; register SEI's relations"


def test_the_spine_is_already_there() -> None:
    """The §3 spine is the part SEI DOES publish everywhere, and it is what makes the rest a rename
    rather than a rebuild. A valuation has no provider id of its own: the account and the day are
    the row, and the book resolves `account_id` from `account_external_id`."""
    for model, _ in _needs():
        published = _published(model)
        if model == "valuations":
            assert {
                "account_external_id",
                "as_of_date",
                "key",
                "lineage",
                "data",
            } <= published
            assert "external_id" not in published
            continue
        assert {"external_id", "key", "lineage", "data"} <= published, model
