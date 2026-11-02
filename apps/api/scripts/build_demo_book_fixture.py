"""Build the anonymised, source-shaped demo client book.

The source exports are intentionally not committed: they contain a client's name and provider
account numbers. This builder strips both, collapses report-grain duplication, and emits the small
deterministic fixture consumed by ``app.demo_seed``.

Usage:
    uv run python scripts/build_demo_book_fixture.py \
      --accounts /path/to/accounts.csv \
      --holdings /path/to/holdings.csv \
      --transactions /path/to/transactions.csv \
      --performance /path/to/performance.xlsx
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

ACCOUNT_KEYS = {
    "MSP000060": ("retirement", "SIPP", "Retirement Portfolio"),
    "MSP000011": ("medium_term", "ISA", "Medium-Term ISA"),
    "MSP000010": ("short_term", "GIA", "Short-Term Investment Account"),
    "MSP000041": ("international", "TPOFFBOND", "International Investment Bond"),
}

TRANSACTION_TYPES = {
    "Acquisition Lot Adjustment": "adjustment",
    "Realized Gain/Loss Adjustment": "adjustment",
    "FX-Spot": "adjustment",
    "Cash Dividend": "dividend",
    "Buy": "buy",
    "Sell": "sell",
    "Currency Interest Posting": "interest",
    "Periodic Fee": "fee",
    "Rights Distribution": "corporate_action",
    "Free Receipt": "transfer_in",
    "Expiry Of Rights": "corporate_action",
    "Stock Split": "corporate_action",
    "Free Delivery": "transfer_out",
    "Bonus Issue": "corporate_action",
    "Interest Payment": "interest",
    "Cash Disbursement": "withdrawal",
    "Cash Receipt": "deposit",
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _number(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    return float(Decimal(str(value)))


def _date(value: str, fmt: str = "%Y-%m-%d") -> str:
    return dt.datetime.strptime(value, fmt).date().isoformat()


def _identity(row: dict[str, str], account_key: str) -> str:
    """Stable identity for a source row after its personal/account labels are removed."""
    ignored = {
        "Account Number",
        "Account Display Name",
        "account_number",
        "account_display_name",
        "account_registration_name",
    }
    body = json.dumps(
        {
            "account": account_key,
            **{key: value for key, value in row.items() if key not in ignored},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()[:20]


def _asset_type(name: str, isin: str, ticker: str) -> str:
    upper = name.upper()
    if not isin and (ticker.endswith("XX") or upper in {"POUND STERLING", "EURO", "US DOLLAR"}):
        return "Cash"
    if "GILT" in upper or "BOND" in upper or "FIXED INCOME" in upper:
        return "Fixed income"
    if "FUND" in upper or isin.startswith("GB00"):
        return "Fund"
    return "Equity"


def _position_currency(name: str, ticker: str) -> str:
    currencies = {
        "POUND STERLING": "GBP",
        "US DOLLAR": "USD",
        "EURO": "EUR",
        "HONG KONG DOLLAR": "HKD",
        "SWISS FRANC": "CHF",
    }
    return currencies.get(
        name.upper(), ticker.removesuffix("XX") if ticker.endswith("XX") else "GBP"
    )


def _holdings(
    rows: list[dict[str, str]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_account: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["Account Number"] in ACCOUNT_KEYS:
            by_account[row["Account Number"]].append(row)

    result: dict[str, list[dict[str, Any]]] = {}
    valuations: dict[str, list[dict[str, Any]]] = {}
    for account_number, account_rows in by_account.items():
        # The report repeats one position for each servicing team. That is visibility metadata,
        # not another holding: collapse on the position values while deliberately ignoring the
        # final account_dsr_team_id column.
        unique: dict[tuple[str, ...], dict[str, str]] = {}
        for row in account_rows:
            key = tuple(value for name, value in row.items() if name != "account_dsr_team_id")
            unique.setdefault(key, row)

        by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in unique.values():
            by_date[_date(row["Position as of Date"], "%d-%b-%Y")].append(row)
        latest = max(by_date)
        positions: list[dict[str, Any]] = []
        for row in by_date[latest]:
            name = row["Fund Instrument Name"].strip()
            isin = row["ISIN"].strip()
            ticker = row["Ticker"].strip()
            value = _number(row["Settled Market Value (ABC)"]) or 0.0
            positions.append(
                {
                    "key": isin or ticker or hashlib.sha256(name.encode()).hexdigest()[:12],
                    "name": name.title() if name.isupper() else name,
                    "isin": isin or None,
                    "sedol": row["SEDOL"].strip() or None,
                    "ticker": ticker or None,
                    "asset_type": _asset_type(name, isin, ticker),
                    "currency": _position_currency(name, ticker),
                    "quantity": _number(row["Settled Quantity"]),
                    "value": value,
                    "price": _number(row["Display Price"]),
                    "as_of": latest,
                }
            )
        total = sum(position["value"] for position in positions)
        for position in positions:
            position["allocation"] = round(position["value"] / total * 100, 6) if total else 0
        positions.sort(key=lambda position: (-position["value"], position["name"]))
        result[account_number] = positions
        valuations[account_number] = [
            {
                "date": date,
                "value": round(
                    sum(_number(row["Settled Market Value (ABC)"]) or 0 for row in date_rows), 2
                ),
                "cash_value": round(
                    sum(
                        _number(row["Settled Market Value (ABC)"]) or 0
                        for row in date_rows
                        if _asset_type(
                            row["Fund Instrument Name"].strip(),
                            row["ISIN"].strip(),
                            row["Ticker"].strip(),
                        )
                        == "Cash"
                    ),
                    2,
                ),
            }
            for date, date_rows in sorted(by_date.items())
        ]
    return result, valuations


def _transactions(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        account_number = row["Account Number"]
        if account_number not in ACCOUNT_KEYS:
            continue
        identity = _identity(row, ACCOUNT_KEYS[account_number][0])
        if identity in seen:
            continue
        seen.add(identity)
        source_type = row["Transaction Type"]
        canonical = TRANSACTION_TYPES.get(source_type)
        if canonical is None:
            raise ValueError(f"unmapped transaction type: {source_type}")
        result[account_number].append(
            {
                "key": identity,
                "type": canonical,
                "source_type": source_type,
                "portfolio_name": row["Portfolio Name"].strip() or None,
                "asset_name": row["Asset Name"].strip() or None,
                "isin": row["ISIN"].strip() or None,
                "issue_currency": row["Issue Currency"].strip() or None,
                "display_price": _number(row["Display Price"]),
                "amount": _number(row["Net Amount (SC)"]),
                "transaction_amount": _number(row["Net Amount (TC)"]),
                "transaction_currency": row["Transaction Currency (TC)"].strip() or None,
                "currency": row["Settlement Currency (SC)"].strip() or "GBP",
                "quantity": _number(row["Net Transaction Quantity"]),
                "price": _number(row["Transaction Price"]),
                "exchange_rate": _number(row["Transaction to Settlement Exchange Rate"]),
                "transaction_date": _date(row["Transaction Date"]),
                "trade_date": _date(row["Trade Date"]) if row["Trade Date"] else None,
                "settlement_date": _date(row["Settlement Date"])
                if row["Settlement Date"]
                else None,
                "reversed": row["Reversal Indicator"].strip().lower() == "yes",
                "provider": row["Provider"].strip() or None,
                "file_date": _date(row["file_date"]),
            }
        )
    for transactions in result.values():
        transactions.sort(
            key=lambda transaction: (transaction["transaction_date"], transaction["key"])
        )
    return dict(result)


def _performance(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - one-off maintainer script
        raise SystemExit("openpyxl is required to rebuild the demo fixture") from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for date, monthly, cumulative, annualised, account_number in sheet.iter_rows(
        min_row=4, values_only=True
    ):
        if not date or account_number not in ACCOUNT_KEYS:
            continue
        result[str(account_number)].append(
            {
                "date": date.date().isoformat(),
                "monthly_return": _number(monthly),
                "cumulative_return": _number(cumulative),
                "annualised_return": _number(annualised),
            }
        )
    for points in result.values():
        points.sort(key=lambda point: point["date"])
    return dict(result)


def build(
    accounts_path: Path,
    holdings_path: Path,
    transactions_path: Path,
    performance_path: Path,
) -> dict[str, Any]:
    accounts = {row["account_number"]: row for row in _rows(accounts_path)}
    positions, valuations = _holdings(_rows(holdings_path))
    transactions = _transactions(_rows(transactions_path))
    performance = _performance(performance_path)
    fixture_accounts: list[dict[str, Any]] = []
    for account_number, (key, product_type, name) in ACCOUNT_KEYS.items():
        source = accounts[account_number]
        fixture_accounts.append(
            {
                "key": key,
                "product_type": product_type,
                "name": name,
                "opened_at": source["account_open_date"],
                "currency": source["account_base_currency"] or "GBP",
                "model_name": source["implementation_model_name"] or None,
                "risk_rating": _number(source["account_risk_rating"]),
                "attributes": {
                    key: source[key]
                    for key in (
                        "account_status_code",
                        "country_code",
                        "investment_restrictions",
                        "solution",
                        "investment_objective",
                        "fee_package_description",
                        "investment_manager",
                        "capacity_type",
                        "account_classification_name",
                        "account_sub_classification_name",
                        "account_sub_type",
                        "tax_status",
                        "account_qualifier_name",
                        "account_restriction",
                        "vat_eligible_flag",
                        "account_inception_date",
                        "account_tax_category",
                        "portfolio_management",
                        "portfolio_purpose",
                        "account_ownership_type",
                    )
                    if source.get(key) not in (None, "")
                },
                "positions": positions.get(account_number, []),
                "valuations": valuations.get(account_number, []),
                "transactions": transactions.get(account_number, []),
                "performance": performance.get(account_number, []),
            }
        )
    _stamp_position_economics(fixture_accounts)
    return {
        "version": 2,
        "description": "Anonymised client book derived from a complete four-account provider extract.",
        "accounts": fixture_accounts,
    }


def _stamp_position_economics(accounts: list[dict[str, Any]]) -> None:
    """The holdings extract has market value and no cost. Same back-out as ``demo_seed``."""
    from decimal import Decimal

    from app.demo_seed import _position_economics

    for account in accounts:
        for position in account["positions"]:
            if Decimal(str(position["value"])) == 0:
                continue
            cost, gain, pct = _position_economics(position, account)
            position["book_cost"] = float(cost)
            position["unrealised_gain"] = float(gain)
            position["unrealised_gain_percent"] = float(pct)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--holdings", type=Path, required=True)
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "app" / "demo_data" / "client_book.json",
    )
    args = parser.parse_args()
    fixture = build(args.accounts, args.holdings, args.transactions, args.performance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2) + "\n")
    print(
        f"wrote {args.output}: {len(fixture['accounts'])} accounts, "
        f"{sum(len(a['positions']) for a in fixture['accounts'])} positions, "
        f"{sum(len(a['transactions']) for a in fixture['accounts'])} transactions"
    )


if __name__ == "__main__":
    main()
