"""The committed dev extract, served in the conformed vocabulary — DISPLAY ONLY, never production.

`docs/seccl.duckdb` is an anonymised, downsampled Seccl extract that has been in this repository
(and in the API image — see the `COPY` in `Dockerfile`) since before the provider pipeline existed.
`app/lake/holdings.py` was written to REPLACE it as the withdrawal gate's source, and did; what was
never finished is the other half, so on any deployment whose provider chain has not run,
`tenant_{uid}.positions` does not exist and every display read degrades to nothing. A client then
signs in to accounts with a real OLTP `value` beside an empty holdings table, an empty activity feed
and a flat chart — which reads as "you own nothing", not as "the warehouse has not been built".

So this serves the display reads off the extract — holdings, the summary, the series, transactions
and Spotlight's capped search — under two rules that keep it from becoming the
hazard `holdings.py`'s docstring spells out:

* **Display only.** The withdrawal gate calls `firm_holdings` directly and does not come through
  here, so it still REFUSES when it cannot read the firm's own slice. "We can't tell" must never
  become "yes" for money leaving; it is perfectly allowed to become "here is the dev extract" for a
  chart. The two callers already sit either side of that line in `crm._firm_read`, which is why the
  fallback belongs there and not inside `firm_holdings`.
* **Never in production.** `app_stage == 'production'` disables it outright, the posture the email
  send guard takes: a real client must never be shown someone else's anonymised numbers, and the
  absence of a warehouse in production is an operational fault to fix rather than to paper over.

The extract keeps REAL Seccl account ids, which is the only reason this works at all: the ids on
`platform.links` resolve straight into it. It carries no tenancy, so callers pass the ids they have
already authorised through RLS and nothing else is readable — the same contract `firm_holdings`
documents.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Where the extract lives. The image copies it to `docs/seccl.duckdb` under the workdir; a dev shell
# runs from `apps/api`, some way below the repository root. Searched upward rather than counted
# `parents[n]`, which is the same off-by-one every time somebody moves a module — and which fails
# by reporting "no extract", indistinguishable from the empty warehouse this exists to cover for.
_RELATIVE = Path("docs/seccl.duckdb")
# The extract is that custodian's dump. `_firm_read` asks `covers()` rather than naming one
# itself — the display-path tripwire in `test_lake_holdings` forbids those literals in crm.py.
_EXTRACT_PROVIDER = "seccl"


def _candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [_RELATIVE, *(parent / _RELATIVE for parent in here.parents)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def path() -> Path | None:
    """The extract, if this deployment has one and is allowed to read it."""
    if settings.app_stage == "production":
        return None
    if os.getenv("LAKE_FIXTURE_DISABLED"):
        return None
    for candidate in _candidates():
        if candidate.is_file():
            return candidate
    return None


def available() -> bool:
    return path() is not None


def covers(provider: str) -> bool:
    """Whether this extract can stand in for an unreadable slice of `provider`.

    Only the extract's own custodian: a SEI outage must not be papered over with Seccl numbers.
    """
    return provider == _EXTRACT_PROVIDER


# The extract's own column names, mapped onto what `holdings.py` publishes. `asset_balances_daily`
# is the per-account/per-asset/per-day valuation table, so the LATEST date per account is the
# current holding and the SUM per date is the value series — one relation answering two questions,
# which is also why the chart and the table cannot disagree.
# CASH IS A SEPARATE TABLE in the extract, and leaving it out is not a cosmetic omission: every
# asset row carries an ISIN, so a derived "no ISIN means cash" test yields nothing, the allocation
# donut sums to 100% of the invested assets alone and the account's Cash figure reads £0 beside a
# real balance. `cash_balances_daily` is unioned in as a position of its own so the donut, the cash
# figure and the account total all agree — the same reason the conformed layer publishes Seccl's
# `positionType` rather than inferring one.
_HOLDINGS_SQL = """
WITH latest AS (
    SELECT account_id, max(date) AS date
    FROM {seccl}.asset_balances_daily
    WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
    GROUP BY account_id
),
latest_cash AS (
    SELECT account_id, max(date) AS date
    FROM {seccl}.cash_balances_daily
    WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
    GROUP BY account_id
),
rows AS (
    SELECT b.account_id      AS account_external_id,
           b.asset_id,
           b.asset_name,
           b.isin,
           'Stock'           AS asset_type,
           CAST(b.valuation AS DOUBLE)       AS value,
           CAST(b.total_quantity AS DOUBLE)  AS quantity,
           CAST(b.price AS DOUBLE)           AS price,
           CAST(b.total_book_cost AS DOUBLE) AS book_cost,
           CAST(b.growth AS DOUBLE)          AS growth,
           CAST(b.growth_pct AS DOUBLE)      AS growth_pct,
           b.date                            AS as_of
    FROM {seccl}.asset_balances_daily b
    JOIN latest l ON l.account_id = b.account_id AND l.date = b.date
    WHERE b.valuation IS NOT NULL AND b.valuation <> 0
    UNION ALL
    -- Cash has no instrument, no book cost and no gain: it is worth what it is. Nulls rather than
    -- zeros, so a growth column shows nothing instead of an invented 0%.
    SELECT c.account_id, NULL, 'Cash', NULL, 'Cash',
           CAST(c.balance AS DOUBLE), NULL, NULL, NULL, NULL, NULL, c.date
    FROM {seccl}.cash_balances_daily c
    JOIN latest_cash l ON l.account_id = c.account_id AND l.date = c.date
    WHERE c.balance IS NOT NULL AND c.balance <> 0
)
SELECT *,
       CAST(value / nullif(sum(value) OVER (PARTITION BY account_external_id), 0) * 100
            AS DOUBLE) AS allocation
FROM rows
ORDER BY account_external_id, value DESC
"""

_SERIES_SQL = """
SELECT account_id            AS account_external_id,
       date,
       CAST(sum(valuation) AS DOUBLE) AS value
FROM {seccl}.asset_balances_daily
WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
  AND valuation IS NOT NULL
GROUP BY account_id, date
HAVING sum(valuation) <> 0
ORDER BY date
"""

# `movement_type` carries the canonical vocabulary in the conformed reader, because the activity
# classifier matches needles against movement_type/type/sub_type together. The extract has all
# three of the vendor's own words, so each goes in its own slot and the concatenation still matches.
_TRANSACTIONS_SQL = """
SELECT account_id              AS account_external_id,
       transaction_date        AS date,
       transaction_type        AS type,
       transaction_sub_type    AS sub_type,
       movement_type,
       asset_name,
       isin,
       CAST(amount AS DOUBLE)  AS amount,
       CAST(quantity AS DOUBLE) AS quantity,
       CAST(price AS DOUBLE)   AS price,
       currency,
       status
FROM {seccl}.transactions
WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY transaction_date DESC NULLS LAST
LIMIT ?
"""


def _read(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    import duckdb

    location = path()
    if location is None:
        return []
    conn = duckdb.connect(str(location), read_only=True)
    try:
        # The extract's catalog and its schema are BOTH named `seccl` (DuckDB names a catalog after
        # the file), so a bare `seccl.transactions` is ambiguous and binds to neither. Qualified
        # from the file's own stem rather than hardcoded, so a renamed extract still resolves.
        qualified = f'"{location.stem}"."seccl"'
        cursor = conn.execute(sql.format(seccl=qualified), params)
        names = [d[0] for d in cursor.description or []]
        return [
            {name: _jsonable(value) for name, value in zip(names, row, strict=True)}
            for row in cursor.fetchall()
        ]
    except Exception as exc:  # noqa: BLE001 - a stale extract must not fail a page
        logger.warning("dev extract read failed: %s", str(exc).splitlines()[0][:200])
        return []
    finally:
        conn.close()


async def holdings(external_account_ids: list[str]) -> list[dict[str, Any]]:
    if not external_account_ids or not available():
        return []
    # Twice: the assets branch and the cash branch each bind the id list.
    return await asyncio.to_thread(
        _read, _HOLDINGS_SQL, [external_account_ids, external_account_ids]
    )


def _is_current(row: Mapping[str, Any]) -> bool:
    """The display rule from ``crm._is_current_holding``: a negative quantity is not a holding.

    Cash carries no quantity and stays. Duplicated here so the extract fallback does not import
    the router that calls it.
    """
    quantity = row.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int | float | Decimal):
        return True
    return quantity >= 0


def matching_holdings(rows: list[dict[str, Any]], q: str, limit: int) -> list[dict[str, Any]]:
    """The extract's answer to Spotlight: name or ISIN, current positions only, value order, capped.

    Same two fields and the same quantity rule as ``_SEARCH_HOLDINGS_SQL``. The extract is small,
    so the filter runs here rather than as a second dialect of that statement.
    """
    term = q.strip().lower()
    if not term or limit < 1:
        return []

    def sort_key(row: dict[str, Any]) -> tuple[bool, float, str]:
        value = row.get("value")
        number = (
            float(value)
            if isinstance(value, int | float | Decimal) and not isinstance(value, bool)
            else None
        )
        # None last, then largest value, then a stable id — the warehouse's ORDER BY.
        return (number is None, -(number or 0.0), str(row.get("asset_id") or ""))

    matched = [
        row
        for row in rows
        if _is_current(row)
        and (
            term in str(row.get("asset_name") or "").lower()
            or term in str(row.get("isin") or "").lower()
        )
    ]
    matched.sort(key=sort_key)
    return matched[:limit]


async def search_holdings(
    external_account_ids: list[str], *, q: str, limit: int
) -> list[dict[str, Any]]:
    """Spotlight over the extract, when the firm's own slice cannot be read."""
    return matching_holdings(await holdings(external_account_ids), q, limit)


async def holdings_summary(external_account_ids: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
    """The summary shape off the extract, so a listings cash figure can fall back too."""
    from app.lake.holdings import summarise_positions

    return summarise_positions(await holdings(external_account_ids))


async def value_series(external_account_ids: list[str]) -> list[dict[str, Any]]:
    if not external_account_ids or not available():
        return []
    return await asyncio.to_thread(_read, _SERIES_SQL, [external_account_ids])


async def transactions(external_account_ids: list[str], *, limit: int = 50) -> list[dict[str, Any]]:
    if not external_account_ids or not available():
        return []
    return await asyncio.to_thread(
        _read, _TRANSACTIONS_SQL, [external_account_ids, max(1, min(limit, 500))]
    )


# Which fixture reader answers which conformed reader, so `crm._firm_read` can fall back without
# knowing anything about either. Keyed by the conformed function itself: a rename moves both.
def reader_for(conformed: Any) -> Any | None:
    from app.lake import holdings as lake_holdings

    return {
        lake_holdings.firm_holdings: holdings,
        lake_holdings.firm_holdings_summary: holdings_summary,
        lake_holdings.firm_value_series: value_series,
        lake_holdings.firm_transactions: transactions,
        # Absent, an unreadable slice searched nothing and the palette reported no holdings
        # while the holdings page — firm_holdings, which is mapped — still fell back here.
        lake_holdings.search_firm_holdings: search_holdings,
    }.get(conformed)
