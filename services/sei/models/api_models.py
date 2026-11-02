"""``raw.api_models`` — the name and type of each model the accounts already use.

SEI's own model reports (``ModelsIntExt``, ``ModelAlloc``) are declared and empty on
every drop, so the catalogue is discovered from ``Accounts.MODEL_ID``. That id is
all most of those models have: the only name in the file, ``IMPLEMENTATION_MODEL_NAME``,
is blank on the majority.

There is no model-by-id read. ``POST .../model/assignment/byAccount`` describes the
model on one portfolio: id, name, type, tolerance, rebalance schedule. It does not
describe a target return, an ongoing charge, or the target holdings, so this table
cannot mint a model version. One call per distinct model is enough, against one
account that names it. A Capital portfolio is preferred; an Income portfolio is
rejected by the service.

Disabled when ``SEI_API_BASE_URL`` is blank: an empty typed table, and
``platform.models`` keeps the file's name.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa
from interlace import model

from sei_sync.firms import resolve_firm_ids
from sei_sync.pull import fetch_model_assignments
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

SCHEMA: dict[str, pa.DataType] = {
    "firm_id": pa.string(),
    "model_id": pa.string(),
    "model_name": pa.string(),
    "model_type": pa.string(),
    "model_type_code": pa.string(),
    "tolerance_multiplier": pa.decimal128(18, 6),
    "schedule_id": pa.string(),
    "schedule_name": pa.string(),
    "investment_objective_id": pa.string(),
    "sleeve_level": pa.string(),
    "as_of_date": pa.date32(),
    "fetched_at": pa.timestamp("us", tz="UTC"),
}

# Capital, not Income. The assignment read refuses an income portfolio.
_CAPITAL_PORTFOLIO = "189"


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


def select_targets(handle: Any, firms: set[str]) -> list[tuple[str, str, str, str]]:
    """One ``(firm, model, account, portfolio)`` per distinct model.

    The representative portfolio prefers type 189 (Capital). The tie-break is the
    account number, then the portfolio id, so two runs pick the same portfolio.
    """
    if handle is None or not firms:
        return []
    table = handle.table()
    columns = {name.upper(): name for name in table.column_names}
    needed = ("FIRM_ID", "MODEL_ID", "ACCOUNT_NUMBER", "PORTFOLIO_ID")
    if any(columns.get(name) is None for name in needed):
        logger.warning("sei raw.api_models: raw.accounts is missing %s", needed)
        return []
    ptype_col = columns.get("PORTFOLIO_TYPE")

    def col(name: str) -> list[Any]:
        return list(table.column(columns[name]).to_pylist())

    firms_col = col("FIRM_ID")
    models = col("MODEL_ID")
    accounts = col("ACCOUNT_NUMBER")
    portfolios = col("PORTFOLIO_ID")
    ptypes = col("PORTFOLIO_TYPE") if ptype_col else [None] * len(firms_col)

    best: dict[tuple[str, str], tuple[tuple[int, str, str], tuple[str, str, str, str]]] = {}
    for firm, model_id, account, portfolio, ptype in zip(
        firms_col, models, accounts, portfolios, ptypes, strict=True
    ):
        firm_id = _text(firm)
        mid = _text(model_id)
        account_number = _text(account)
        portfolio_id = _text(portfolio)
        if not firm_id or not mid or not account_number or not portfolio_id:
            continue
        if firm_id not in firms:
            continue
        rank = (
            0 if _text(ptype) == _CAPITAL_PORTFOLIO else 1,
            account_number,
            portfolio_id,
        )
        target = (firm_id, mid, account_number, portfolio_id)
        key = (firm_id, mid)
        current = best.get(key)
        if current is None or rank < current[0]:
            best[key] = (rank, target)
    return [item[1] for item in sorted(best.values(), key=lambda item: item[1])]


def conform(rows: list[dict[str, Any]], *, as_of: date, fetched_at: datetime) -> pa.Table:
    """Assignment rows to the conformed table. A row with no model id is dropped."""
    columns: dict[str, list[Any]] = {name: [] for name in SCHEMA}
    for row in rows:
        model_id = _text(row.get("_model_id")) or _text(row.get("modelId"))
        firm_id = _text(row.get("_firm_id"))
        if not model_id or not firm_id:
            continue
        columns["firm_id"].append(firm_id)
        columns["model_id"].append(model_id)
        columns["model_name"].append(_text(row.get("modelName")))
        columns["model_type"].append(_text(row.get("modelTypeDesc")))
        columns["model_type_code"].append(_text(row.get("modelType")))
        columns["tolerance_multiplier"].append(_decimal(row.get("toleranceMultiplier")))
        columns["schedule_id"].append(_text(row.get("allowableProcessingSchId")))
        columns["schedule_name"].append(_text(row.get("allowableProcessingSchName")))
        columns["investment_objective_id"].append(_text(row.get("investmentObjectiveId")))
        columns["sleeve_level"].append(_text(row.get("sleeveConfigLevelDesc")))
        columns["as_of_date"].append(as_of)
        columns["fetched_at"].append(fetched_at)
    return pa.table({name: pa.array(values, type=SCHEMA[name]) for name, values in columns.items()})


@model(
    name="raw.api_models",
    strategy="full_merge",
    key=("firm_id", "model_id"),
    depends_on=("raw.accounts", "ref.firms"),
    schedule={"cron": "15 7 * * 2-6"},
    tags=("sei", "raw", "api"),
    description="SEI model name and type, one assignment read per distinct model id",
)
async def raw_api_models(raw_accounts: Any, ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei raw.api_models: SEI_API_BASE_URL unset — API sync disabled")
        return conform([], as_of=now.date(), fetched_at=now)

    firm_ids = resolve_firm_ids(ref_firms)
    if not firm_ids:
        logger.warning("sei raw.api_models: the Reference report named no firm")
        return conform([], as_of=now.date(), fetched_at=now)

    targets = select_targets(raw_accounts, set(firm_ids))
    rows, stats = await fetch_model_assignments(settings, targets)
    if stats.failures:
        logger.warning(
            "sei raw.api_models: %d model(s) unread, e.g. %s",
            len(stats.failures),
            next(iter(stats.failures.items())),
        )
    return conform(rows, as_of=now.date(), fetched_at=now)


__all__ = ["SCHEMA", "conform", "raw_api_models", "select_targets"]
