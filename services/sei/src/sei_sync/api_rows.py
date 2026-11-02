"""Flatten SEI's nested transaction payload into the raw layer's shape.

Kept out of the model module for the usual interlace reason inverted: this is the part
that carries no *semantic* choice about which report to read, so hiding it behind a helper
costs nothing in change detection — the model's own SQL-equivalent (which fields, which
types) is the schema declared here, and a change to it changes this file, which the model
imports and which the tests pin.

Types are declared rather than inferred. SEI sends every money field as a **string**
("132.80"), so leaving inference to pyarrow would type a column by whatever the first
batch happened to hold and drift between runs — the same trap the SFTP raw layer avoids.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

# One row per transaction. `transaction_id` is SEI's own id and the merge key.
SCHEMA = pa.schema(
    [
        pa.field("transaction_id", pa.int64(), nullable=False),
        pa.field("firm_id", pa.string(), nullable=False),
        pa.field("account_number", pa.string()),
        pa.field("account_name", pa.string()),
        pa.field("account_type", pa.int64()),
        pa.field("account_type_description", pa.string()),
        pa.field("external_reference_id", pa.string()),
        pa.field("portfolio_id", pa.int64()),
        pa.field("portfolio_name", pa.string()),
        pa.field("portfolio_type", pa.int64()),
        pa.field("portfolio_type_description", pa.string()),
        pa.field("transaction_type", pa.string()),
        pa.field("transaction_description", pa.string()),
        pa.field("transaction_date", pa.date32()),
        # How far the scan that wrote this row had already asked SEI. The next run
        # starts from the max of this column, not from max(transaction_date): a quiet
        # book would otherwise re-walk every empty month after the last movement.
        pa.field("covered_through", pa.date32()),
        pa.field("trade_date", pa.date32()),
        pa.field("settlement_date", pa.date32()),
        pa.field("statement_date", pa.date32()),
        # The booking date. SEI will not let us FILTER on it, but it is in the payload, and
        # it is the only field that distinguishes a transaction booked late from one dated
        # late — which is what makes the lookback window auditable.
        pa.field("submitted_date", pa.date32()),
        pa.field("submitted_by", pa.string()),
        pa.field("submitted_by_team", pa.string()),
        pa.field("activity_id", pa.int64()),
        pa.field("order_id", pa.int64()),
        pa.field("adjustment_flag", pa.bool_()),
        pa.field("reversal_flag", pa.bool_()),
        pa.field("suppression_flag", pa.bool_()),
        pa.field("base_currency", pa.string()),
        pa.field("local_currency_code", pa.string()),
        pa.field("transaction_quantity", pa.decimal128(28, 8)),
        pa.field("local_transaction_value", pa.decimal128(28, 4)),
        pa.field("local_transaction_gross_amount", pa.decimal128(28, 4)),
        pa.field("local_net_transaction_amount", pa.decimal128(28, 4)),
        pa.field("base_net_transaction_amount", pa.decimal128(28, 4)),
        pa.field("net_charges_amount", pa.decimal128(28, 4)),
        pa.field("net_taxes_amount", pa.decimal128(28, 4)),
        pa.field("local_book_value", pa.decimal128(28, 4)),
        pa.field("base_book_value", pa.decimal128(28, 4)),
        pa.field("global_asset_id", pa.string()),
        pa.field("instrument_id", pa.string()),
        pa.field("isin", pa.string()),
        pa.field("sedol", pa.string()),
        pa.field("cusip", pa.string()),
        pa.field("ticker", pa.string()),
        pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

_MONEY_SCALE = {
    "transaction_quantity": 8,
}
_DEFAULT_SCALE = 4


def _as_date(value: Any) -> date | None:
    """SEI's API dates are ISO (``2026-03-27``), unlike the drop's ``20260327``."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any, scale: int) -> Decimal | None:
    """Money arrives as a string. Quantised to the declared scale, because pyarrow rejects
    a Decimal with more places than its type allows rather than rounding it."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def flatten(payload: list[dict[str, Any]], *, fetched_at: datetime) -> pa.Table:
    """Nested API rows -> the declared flat schema.

    An entry missing ``transactionId`` is dropped, not defaulted: it is the merge key, and
    a NULL there would collapse every such row onto one.
    """
    rows: list[dict[str, Any]] = []
    for entry in payload:
        detail = entry.get("transactionDetails") or {}
        account = entry.get("accountDetails") or {}
        portfolio = detail.get("portfolioDetails") or {}
        asset = detail.get("assetDetails") or {}

        transaction_id = _as_int(detail.get("transactionId"))
        if transaction_id is None:
            continue

        row: dict[str, Any] = {
            "transaction_id": transaction_id,
            "firm_id": str(entry.get("_firm_id") or ""),
            "account_number": account.get("accountNumber"),
            "account_name": account.get("accountName"),
            "account_type": _as_int(account.get("accountType")),
            "account_type_description": account.get("accountTypeDescription"),
            "external_reference_id": account.get("externalReferenceId"),
            "portfolio_id": _as_int(portfolio.get("portfolioId")),
            "portfolio_name": portfolio.get("portfolioName"),
            "portfolio_type": _as_int(portfolio.get("portfolioType")),
            "portfolio_type_description": portfolio.get("portfolioTypeDescription"),
            "transaction_type": detail.get("transactionType"),
            "transaction_description": detail.get("transactionDescription"),
            "transaction_date": _as_date(detail.get("transactionDate")),
            "covered_through": None,
            "trade_date": _as_date(detail.get("tradeDate")),
            "settlement_date": _as_date(detail.get("settlementDate")),
            "statement_date": _as_date(detail.get("statementDate")),
            "submitted_date": _as_date(detail.get("submittedDate")),
            "submitted_by": detail.get("submittedBy"),
            "submitted_by_team": detail.get("submittedByTeam"),
            "activity_id": _as_int(detail.get("activityId")),
            "order_id": _as_int(detail.get("orderId")),
            "adjustment_flag": _as_bool(detail.get("adjustmentFlag")),
            "reversal_flag": _as_bool(detail.get("reversalFlag")),
            "suppression_flag": _as_bool(detail.get("suppressionFlag")),
            "base_currency": detail.get("baseCurrency"),
            "local_currency_code": detail.get("localCurrencyCode"),
            "global_asset_id": asset.get("globalAssetId"),
            "instrument_id": asset.get("instrumentId"),
            "isin": asset.get("isin"),
            "sedol": asset.get("sedol"),
            "cusip": asset.get("cusip"),
            "ticker": asset.get("ticker"),
            "fetched_at": fetched_at,
        }
        for name, source in (
            ("transaction_quantity", "transactionQuantity"),
            ("local_transaction_value", "localTransactionValue"),
            ("local_transaction_gross_amount", "localTransactionGrossAmount"),
            ("local_net_transaction_amount", "localNetTransactionAmount"),
            ("base_net_transaction_amount", "baseNetTransactionAmount"),
            ("net_charges_amount", "netChargesAmount"),
            ("net_taxes_amount", "netTaxesAmount"),
            ("local_book_value", "localBookValue"),
            ("base_book_value", "baseBookValue"),
        ):
            row[name] = _as_decimal(detail.get(source), _MONEY_SCALE.get(name, _DEFAULT_SCALE))
        rows.append(row)

    return pa.Table.from_pylist(rows, schema=SCHEMA)
