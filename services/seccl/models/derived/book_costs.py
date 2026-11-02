"""`derived.book_costs` — the cost basis and realised gain of every movement.

A PYTHON model, because the computation is a recurrence and not an aggregation: a holding's book
cost is a running average, so each row's answer depends on its own previous output.
`seccl_sync.book_costs` holds the arithmetic (and the argument for it); this is the thin part that
reads the warehouse and writes a table.

**A THIRD SCHEMA, and why.** `raw.*` is what the provider sent, `platform.*` is the conformed shape
`docs/platform-model.md` §3 defines and the sink merges. This is neither: it is a Seccl-specific
intermediate that exists so `platform.transactions` can carry a `book_cost` and a `realised_gain`
without computing them itself. Putting it in `platform` would claim it is part of a contract every
provider meets, and putting it in `raw` would claim Seccl sent it.

**It reads `raw.transactions`, NOT `platform.transactions`** — which is the only ordering that
works: the conformed model joins THIS one for its gain, so reading it back would be a cycle.
Everything the recurrence needs (movement type, quantity, consideration, `information`, status) is
on the raw row already, and the one thing that is not — the wrapper, which decides whether share
matching applies — comes from `raw.accounts` the same way the conformed model gets its
`product_key`.

`strategy: replace`, deliberately. The pool is a function of the WHOLE history, so a new
transaction in the middle of one (a backdated correction, which Seccl does book) changes every row
after it. An incremental merge would leave those rows stating a basis that no longer follows from
the ledger, and a wrong cost basis is a wrong CGT position. Recomputing the book is cheap next to
being wrong.
"""

from __future__ import annotations

import logging
from typing import Any

import pyarrow as pa
from interlace import model

from seccl_sync import book_costs as bc

logger = logging.getLogger("seccl_sync.models")

#: Declared, not inferred. A firm with no settled orders produces no rows, and an inferred schema
#: over zero rows gives Arrow's null type — which the warehouse drops, so the conformed model's
#: join fails to bind on precisely the firm that has nothing to join. Same rule as
#: `raw.fee_structures`.
_SCHEMA = pa.schema(
    [
        ("transaction_id", pa.string()),
        ("account_id", pa.string()),
        ("isin", pa.string()),
        ("book_cost", pa.float64()),
        ("total_book_cost", pa.float64()),
        ("total_quantity", pa.float64()),
        ("book_price", pa.float64()),
        ("realised_gain", pa.float64()),
        ("matched_quantity", pa.float64()),
        ("current", pa.bool_()),
    ]
)


def _rows(handle: Any) -> list[dict[str, Any]]:
    return handle.table().to_pylist() if handle is not None else []


def _wrapper_by_account(accounts: Any) -> dict[str, str | None]:
    """Each account's wrapper, which is what decides whether share matching applies.

    Read from `wrapper_detail.wrapperType` and NOT from `account_type`: verified against staging,
    `account_type` is the literal 'Wrapper' on all 376 accounts — it is Seccl's account CLASS —
    which is the same trap `platform/accounts.sql` records, where the old coalesce order resolved
    every account's product to 'Wrapper' and the real values were never read. Getting it wrong here
    would silently switch share matching off for every GIA in the book.
    """
    out: dict[str, str | None] = {}
    for row in _rows(accounts):
        detail = row.get("wrapper_detail")
        if isinstance(detail, str):
            import json

            try:
                detail = json.loads(detail)
            except (TypeError, ValueError):
                detail = None
        wrapper = detail.get("wrapperType") if isinstance(detail, dict) else None
        out[str(row.get("id") or "")] = wrapper
    return out


@model(
    name="derived.book_costs",
    strategy="replace",
    key=("transaction_id",),
    depends_on=("raw.transactions", "raw.accounts"),
    schedule={"cron": "45 2 * * *"},
    tags=("seccl", "derived"),
    description="Pooled cost basis + realised gain per movement (share-matched on a GIA)",
)
async def derived_book_costs(raw_transactions: Any, raw_accounts: Any) -> pa.Table:
    movements = bc.movements_from_rows(_rows(raw_transactions), _wrapper_by_account(raw_accounts))
    rows = bc.compute_all(movements)
    # Logged at INFO because "no gains" and "this firm has no chargeable disposals" are the same
    # screen, and only the log tells them apart.
    logger.info("derived.book_costs: %s", bc.summarise(movements, rows).describe())
    return pa.Table.from_pylist(bc.as_records(rows), schema=_SCHEMA)
