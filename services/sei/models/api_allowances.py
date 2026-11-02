"""``raw.api_allowances`` — the ISA and JISA contribution block on an account.

The file already states the client's subscription used
(``Clients.ISA_CONTRIBUTION_TO_DATE``) and the headroom left
(``TOTAL_REMAINING_ISA_HEADROOM_AMOUNT``). What it does not state is the tax year,
and it does not state a SIPP contribution at all.

``POST .../retrieveAccountDetails`` returns ``isaJisaContributionDetails``: the
client-level ISA and JISA limit, year-to-date, remaining, and the tax year
(``2026-2027``). Both sides come back on every ISA and every JISA. A SIPP — an
Investment Account classified as a pension — returns that block as null, and the
SIPP profile service is not deployed on the gateway. The file's
``TOTAL_SIPP_AMOUNT`` and crystallised columns are unpopulated. There is no
source for a SIPP annual allowance, so this model does not request SIPPs and
does not invent the statutory pension allowance.

The book still prefers used + remaining over the statutory limit field. A
flexible ISA's replacement room makes that sum larger than 20,000, and storing
the statutory field as the limit would show those clients over-subscribed.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa
from interlace import model

from sei_sync.api import AccountRef
from sei_sync.firms import resolve_firm_ids
from sei_sync.pull import fetch_account_allowances
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

SCHEMA: dict[str, pa.DataType] = {
    "firm_id": pa.string(),
    "account_number": pa.string(),
    "tax_year": pa.string(),
    "isa_limit": pa.decimal128(20, 4),
    "isa_year_to_date": pa.decimal128(20, 4),
    "isa_remaining": pa.decimal128(20, 4),
    "jisa_limit": pa.decimal128(20, 4),
    "jisa_year_to_date": pa.decimal128(20, 4),
    "jisa_remaining": pa.decimal128(20, 4),
    "withdrawal_to_date": pa.decimal128(20, 4),
    "flexible": pa.string(),
    "subscription_status": pa.string(),
    "as_of_date": pa.date32(),
    "fetched_at": pa.timestamp("us", tz="UTC"),
}

# Stocks and Shares ISA, Stocks and Shares Junior ISA. A SIPP is ACCOUNT_TYPE 157.
_ALLOWANCE_WRAPPERS = frozenset({"172", "173"})

# SEI spells the JISA limit with a lower-case "c". The other JISA fields are camel case.
_JISA_LIMIT = "jisaClientLevelcontributionlimit"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: Any) -> Decimal | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def select_accounts(handle: Any, firms: set[str]) -> list[AccountRef]:
    """Distinct ISA and JISA accounts. A SIPP is not requested — see the module note."""
    if handle is None or not firms:
        return []
    table = handle.table()
    columns = {name.upper(): name for name in table.column_names}
    needed = ("FIRM_ID", "ACCOUNT_NUMBER", "ACCOUNT_TYPE")
    if any(columns.get(name) is None for name in needed):
        logger.warning("sei raw.api_allowances: raw.accounts is missing %s", needed)
        return []

    def col(name: str) -> list[Any]:
        return list(table.column(columns[name]).to_pylist())

    seen: set[tuple[str, str]] = set()
    for firm, account, wrapper in zip(
        col("FIRM_ID"), col("ACCOUNT_NUMBER"), col("ACCOUNT_TYPE"), strict=True
    ):
        firm_id = _text(firm)
        account_number = _text(account)
        if not firm_id or not account_number or firm_id not in firms:
            continue
        if _text(wrapper) not in _ALLOWANCE_WRAPPERS:
            continue
        seen.add((firm_id, account_number))
    return [AccountRef(firm_id=firm, account_number=account) for firm, account in sorted(seen)]


def conform(rows: list[dict[str, Any]], *, as_of: date, fetched_at: datetime) -> pa.Table:
    """Contribution blocks to the conformed table. A row with no account number is dropped."""
    columns: dict[str, list[Any]] = {name: [] for name in SCHEMA}
    for row in rows:
        firm_id = _text(row.get("_firm_id"))
        account_number = _text(row.get("_account_number"))
        if not firm_id or not account_number:
            continue
        columns["firm_id"].append(firm_id)
        columns["account_number"].append(account_number)
        columns["tax_year"].append(_text(row.get("taxYear")))
        columns["isa_limit"].append(_decimal(row.get("isaClientLevelContributionLimit")))
        columns["isa_year_to_date"].append(
            _decimal(row.get("isaClientLevelYearToDateContribution"))
        )
        columns["isa_remaining"].append(_decimal(row.get("isaClientLevelRemainingContribution")))
        columns["jisa_limit"].append(_decimal(row.get(_JISA_LIMIT)))
        columns["jisa_year_to_date"].append(
            _decimal(row.get("jisaClientLevelYearToDateContribution"))
        )
        columns["jisa_remaining"].append(_decimal(row.get("jisaClientLevelRemainingContribution")))
        columns["withdrawal_to_date"].append(_decimal(row.get("clientYearToDateWithdrawal")))
        columns["flexible"].append(_text(row.get("isFlexibleIsaAccount")))
        columns["subscription_status"].append(_text(row.get("accountSubscriptionStatus")))
        columns["as_of_date"].append(as_of)
        columns["fetched_at"].append(fetched_at)
    return pa.table({name: pa.array(values, type=SCHEMA[name]) for name, values in columns.items()})


@model(
    name="raw.api_allowances",
    strategy="full_merge",
    key=("firm_id", "account_number"),
    depends_on=("raw.accounts", "ref.firms"),
    schedule={"cron": "15 7 * * 2-6"},
    tags=("sei", "raw", "api"),
    description="SEI ISA and JISA contribution allowance, from account details, per account",
)
async def raw_api_allowances(raw_accounts: Any, ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei raw.api_allowances: SEI_API_BASE_URL unset — API sync disabled")
        return conform([], as_of=now.date(), fetched_at=now)

    firm_ids = resolve_firm_ids(ref_firms)
    if not firm_ids:
        logger.warning("sei raw.api_allowances: the Reference report named no firm")
        return conform([], as_of=now.date(), fetched_at=now)

    accounts = select_accounts(raw_accounts, set(firm_ids))
    rows, stats = await fetch_account_allowances(settings, accounts)
    if stats.failures:
        logger.warning(
            "sei raw.api_allowances: %d account(s) unread, e.g. %s",
            len(stats.failures),
            next(iter(stats.failures.items())),
        )
    return conform(rows, as_of=now.date(), fetched_at=now)


__all__ = ["SCHEMA", "conform", "raw_api_allowances", "select_accounts"]
