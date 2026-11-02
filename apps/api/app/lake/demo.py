"""Display-only reader for deterministic demo relations in a tenant's own lake.

Money controls never import this module.  They continue to read the provider
slice directly and therefore fail closed while provider refresh is paused.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from app import lake

_HOLDINGS = """
SELECT p.account_external_id, p.instrument_key AS asset_id, p.asset_name, p.isin,
       i.sedol, p.currency, p.position_type AS asset_type,
       p.market_value AS value, p.quantity, p.price, p.book_cost,
       p.unrealised_gain AS growth, p.unrealised_gain_percent AS growth_pct,
       p.allocation, p.as_of_date AS as_of
FROM platform.positions p
LEFT JOIN platform.instruments i ON i.external_id=p.instrument_key
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY p.account_external_id, p.market_value DESC
"""

# A lake first created without cost/gain columns cannot SELECT them. The bare shape is
# everything the overview still needs (value, cash class, allocation) so a missing
# column degrades to "derive the cost" rather than "the client holds nothing".
_HOLDINGS_BARE = """
SELECT p.account_external_id, p.instrument_key AS asset_id, p.asset_name, p.isin,
       i.sedol, p.currency, p.position_type AS asset_type,
       p.market_value AS value, p.quantity, p.price,
       p.allocation, p.as_of_date AS as_of
FROM platform.positions p
LEFT JOIN platform.instruments i ON i.external_id=p.instrument_key
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY p.account_external_id, p.market_value DESC
"""

_SERIES = """
SELECT account_external_id, as_of_date AS date, CAST(market_value AS DOUBLE) AS value
FROM platform.valuations
WHERE account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY as_of_date
"""

# The same predicate as ``lake.holdings._SEARCH_HOLDINGS_SQL``. Spotlight calls that function;
# a seeded book must answer it from this lake, not from the provider catalogue.
_SEARCH = """
SELECT p.account_external_id, p.instrument_key AS asset_id, p.asset_name, p.isin,
       p.market_value AS value
FROM platform.positions p
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
  AND (p.quantity IS NULL OR p.quantity >= 0)
  AND (p.asset_name ILIKE ? ESCAPE '\\' OR p.isin ILIKE ? ESCAPE '\\')
ORDER BY p.market_value DESC NULLS LAST, p.instrument_key
LIMIT ?
"""

_TRANSACTIONS = """
SELECT t.account_external_id, t.transaction_date AS date, t.source_type AS type,
       t.source_subtype AS sub_type, t.type AS movement_type, i.name AS asset_name,
       t.isin, CAST(coalesce(t.net_amount,t.gross_amount) AS DOUBLE) AS amount,
       t.quantity, t.unit_price AS price, t.currency, t.status
FROM platform.transactions t
LEFT JOIN platform.instruments i ON i.external_id=t.instrument_key
WHERE t.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY t.transaction_date DESC
LIMIT ?
"""


def _is_cash_holding(row: Mapping[str, Any]) -> bool:
    return str(row.get("asset_type") or "") == "Cash" or not row.get("isin")


def _implied_return(values: Sequence[float]) -> Decimal:
    """Back out a cost basis the same way the seed does when the workbook column is empty.

    The ISA's daily series jumps 4× because early snapshots were incomplete, which is not a
    return — same ±100% guard as ``demo_seed._cumulative_return``.
    """
    if len(values) >= 2 and values[0] > 0:
        implied = Decimal(str(values[-1])) / Decimal(str(values[0])) - 1
        if abs(implied) <= 1:
            return min(max(implied, Decimal("-0.8")), Decimal("2"))
    return Decimal("0.12")


def apply_missing_economics(
    rows: list[dict[str, Any]], series: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Fill ``book_cost`` / ``growth`` when the lake stored them as NULL or never had the columns.

    Cash is cost = value, gain = 0. Everything else uses the account's valuation series (or
    12% when that move is not a return). Already-numeric figures are left alone, so a
    re-seeded lake is not rewritten on the way out.
    """
    missing = any(row.get("book_cost") is None or row.get("growth") is None for row in rows)
    if not rows or not missing:
        return rows
    by_account: dict[str, list[float]] = defaultdict(list)
    for point in series:
        account = str(point.get("account_external_id") or "")
        try:
            point_value = float(point["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if account:
            by_account[account].append(point_value)
    returns = {account: _implied_return(values) for account, values in by_account.items()}
    for row in rows:
        holding_value = Decimal(str(row.get("value") or 0))
        if _is_cash_holding(row):
            if row.get("book_cost") is None:
                row["book_cost"] = float(holding_value.quantize(Decimal("0.01")))
            if row.get("growth") is None:
                row["growth"] = 0.0
                row["growth_pct"] = 0.0
            continue
        ret = min(
            max(returns.get(str(row.get("account_external_id")), Decimal("0.12")), Decimal("-0.8")),
            Decimal("2"),
        )
        cost = (holding_value / (1 + ret)).quantize(Decimal("0.01")) if (1 + ret) else holding_value
        if cost <= 0:
            cost = holding_value
        gain = (holding_value - cost).quantize(Decimal("0.01"))
        pct = (gain / cost * 100).quantize(Decimal("0.01")) if cost else Decimal("0")
        if row.get("book_cost") is None:
            row["book_cost"] = float(cost)
        if row.get("growth") is None:
            row["growth"] = float(gain)
            if row.get("growth_pct") is None:
                row["growth_pct"] = float(pct)
    return rows


async def _rows(uid: str, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    async with lake.connection(uid) as conn:
        return await conn.rows(sql, params)


async def firm_holdings(
    _provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    try:
        rows = await _rows(tenant_uid, _HOLDINGS, [external_account_ids])
    except Exception as exc:
        # BinderException when the table was created without cost/gain columns. Anything
        # else — lake down, missing relation — must still fail so `_firm_read` degrades.
        message = str(exc)
        if "book_cost" not in message and "unrealised_gain" not in message:
            raise
        rows = await _rows(tenant_uid, _HOLDINGS_BARE, [external_account_ids])
    if any(row.get("book_cost") is None or row.get("growth") is None for row in rows):
        series = await _rows(tenant_uid, _SERIES, [external_account_ids])
        apply_missing_economics(rows, series)
    return rows


async def firm_holdings_summary(
    provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    """The summary shape, aggregated from the demo positions with the shared classifier."""
    from app.lake.holdings import summarise_positions

    return summarise_positions(await firm_holdings(provider, tenant_uid, external_account_ids))


async def firm_value_series(
    _provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    return await _rows(tenant_uid, _SERIES, [external_account_ids])


async def search_firm_holdings(
    _provider: str,
    tenant_uid: str,
    external_account_ids: list[str],
    *,
    q: str,
    limit: int,
) -> list[dict[str, Any]]:
    """First matching positions for Spotlight, from the demo lake rather than the provider slice."""
    if not external_account_ids:
        return []
    from app.lake.holdings import contains_pattern

    pattern = contains_pattern(q)
    return await _rows(tenant_uid, _SEARCH, [external_account_ids, pattern, pattern, max(1, limit)])


async def firm_transactions(
    _provider: str,
    tenant_uid: str,
    external_account_ids: list[str],
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return await _rows(tenant_uid, _TRANSACTIONS, [external_account_ids, limit])


def reader_for(conformed: Any) -> Any | None:
    from app.lake import holdings as lake_holdings

    return {
        lake_holdings.firm_holdings: firm_holdings,
        lake_holdings.firm_holdings_summary: firm_holdings_summary,
        lake_holdings.firm_value_series: firm_value_series,
        lake_holdings.firm_transactions: firm_transactions,
        # Absent, Spotlight searched the provider catalogue for a book that lives here and
        # reported no holdings while the holdings page — which uses firm_holdings — listed them.
        lake_holdings.search_firm_holdings: search_firm_holdings,
    }.get(conformed)
