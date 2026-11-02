"""Account pairs the API models fan out over.

The drop has one row per account portfolio. De-duplicating here is what stops a
sync re-requesting the same account once per portfolio. ``ACCOUNT_OPEN_DATE``
travels with the pair: it is the first day a scan of that firm is worth making.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sei_sync.api import AccountRef, as_day

logger = logging.getLogger("sei_sync.models")


def account_refs(handle: Any, firms: list[str] | None) -> list[AccountRef]:
    """Distinct (firm, account) pairs, with the earliest open date on each.

    ``firms`` is the Reference report. None means do not filter. An unreadable
    report is an empty list and requests nobody.
    """
    if handle is None:
        return []
    table = handle.table()
    columns = {name.upper(): name for name in table.column_names}
    firm_col, account_col = columns.get("FIRM_ID"), columns.get("ACCOUNT_NUMBER")
    if not firm_col or not account_col:
        logger.warning(
            "sei accounts: raw.accounts has no FIRM_ID/ACCOUNT_NUMBER (%s)",
            table.column_names[:8],
        )
        return []

    wanted = None if firms is None else set(firms)
    opened_col = columns.get("ACCOUNT_OPEN_DATE")
    opened_values = table.column(opened_col).to_pylist() if opened_col else [None] * table.num_rows
    earliest: dict[tuple[str, str], date | None] = {}
    for firm, account, opened in zip(
        table.column(firm_col).to_pylist(),
        table.column(account_col).to_pylist(),
        opened_values,
        strict=True,
    ):
        if not firm or not account:
            continue
        firm, account = str(firm).strip(), str(account).strip()
        if wanted is not None and firm not in wanted:
            continue
        day = as_day(opened)
        key = (firm, account)
        previous = earliest.get(key)
        if key not in earliest:
            earliest[key] = day
        elif day is not None and (previous is None or day < previous):
            earliest[key] = day
    return [
        AccountRef(firm_id=firm, account_number=account, opened_on=opened)
        for (firm, account), opened in sorted(earliest.items())
    ]


def opened_by_firm(accounts: list[AccountRef]) -> dict[str, date]:
    """Earliest ``ACCOUNT_OPEN_DATE`` per firm. A firm with none is absent."""
    opened: dict[str, date] = {}
    for account in accounts:
        if account.opened_on is None:
            continue
        current = opened.get(account.firm_id)
        if current is None or account.opened_on < current:
            opened[account.firm_id] = account.opened_on
    return opened
