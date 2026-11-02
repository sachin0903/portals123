"""Uninvested cash for the management report.

Current cash is not a column on the account. The portals sum it from the firm's DuckLake
positions, and this report uses that same summary so a money-market fund stays in cash.
``platform.valuations.cash_value`` is a different balance and is not read here.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.lake.holdings import firm_holdings_summary

_ACCOUNTS_SQL = """
SELECT COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name) AS client_name,
       c.uid AS client_id,
       a.uid AS account_id,
       CAST(p.product_type AS text) AS wrapper,
       m.name AS model,
       a.currency,
       a.value AS market_value,
       l.provider AS provider,
       l.external_id AS external_id
FROM platform.accounts a
JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL
JOIN platform.links l
  ON l.entity_type = 'account'
 AND l.entity_id = a.id
 AND l.link_type = 'primary'
 AND l.tenant_id = a.tenant_id
LEFT JOIN platform.products p ON p.id = a.product_id
LEFT JOIN platform.models m ON m.id = a.model_id
WHERE a.status NOT IN ('draft', 'closed')
ORDER BY client_name, a.uid
"""


def assemble(accounts: list[dict[str, Any]], summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per open account the holdings summary actually returned.

    An account with no position in the slice is omitted: the lake cannot see it, which is
    different from seeing it and finding no cash. Cash is the ``Cash`` class only; the
    summary has already applied the holdings classifier.
    """
    seen: set[str] = set()
    cash: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in summary:
        external_id = str(row.get("account_external_id") or "")
        if not external_id:
            continue
        seen.add(external_id)
        if row.get("klass") != "Cash":
            continue
        value = row.get("value")
        cash[external_id] += Decimal(0) if value is None else Decimal(str(value))

    out: list[dict[str, Any]] = []
    for account in accounts:
        external_id = str(account["external_id"])
        if external_id not in seen:
            continue
        amount = cash.get(external_id, Decimal(0))
        market = account.get("market_value")
        share = None
        if market is not None and Decimal(str(market)) != 0:
            share = (amount / Decimal(str(market))) * Decimal(100)
        out.append(
            {
                "client_name": account.get("client_name"),
                "client_id": account.get("client_id"),
                "account_id": account.get("account_id"),
                "wrapper": account.get("wrapper"),
                "model": account.get("model"),
                "currency": account.get("currency"),
                "market_value": market,
                "cash": amount,
                "cash_share": share,
            }
        )
    return out


async def gather(session: AsyncSession) -> list[dict[str, Any]]:
    """Open accounts this caller can see, with cash from each custodian's slice.

    Raises :class:`HoldingsUnavailable` when a slice cannot be read. The caller writes
    nothing in that case: an empty file would look like a firm that holds no cash.
    """
    tenant_uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = auth.tenant_id()"))
    ).scalar_one()
    accounts = [dict(row) for row in (await session.execute(text(_ACCOUNTS_SQL))).mappings().all()]
    by_provider: dict[str, list[str]] = defaultdict(list)
    for account in accounts:
        by_provider[str(account["provider"])].append(str(account["external_id"]))

    summary: list[dict[str, Any]] = []
    for provider, external_ids in by_provider.items():
        summary.extend(await firm_holdings_summary(provider, str(tenant_uid), external_ids))
    return assemble(accounts, summary)
