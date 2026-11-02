"""IFSL fund look-through workbook parsing and snapshot persistence."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from os.path import basename
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id

MAX_WORKBOOK_BYTES = 25 * 1024 * 1024
MAX_WORKBOOK_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_ASSET_CLASSES = {
    "S": "Equity",
    "B": "Fixed income",
    "CU": "Cash",
    "CA": "Cash",
    "F": "Derivative",
    "FT": "Derivative",
    "FC": "Fund",
    "FL": "Fund",
    "XX": "Structured product",
}


class WorkbookError(ValueError):
    """The uploaded workbook does not satisfy the verified IFSL report contract."""


@dataclass(frozen=True)
class ShareClass:
    code: str
    name: str
    isin: str
    sedol: str | None


@dataclass(frozen=True)
class Position:
    ordinal: int
    isin: str | None
    sedol: str | None
    name: str
    long_name: str | None
    currency: str | None
    asset_group: str | None
    asset_class: str
    country_name: str | None
    country_code: str | None
    maturity_date: date | None
    quantity: Decimal | None
    price: Decimal | None
    prior_price: Decimal | None
    movement_percent: Decimal | None
    book_cost: Decimal | None
    market_value: Decimal
    weight: Decimal


@dataclass(frozen=True)
class NavPoint:
    date: date
    class_code: str
    class_name: str | None
    nav: Decimal
    units_outstanding: Decimal | None
    fund_value: Decimal | None


@dataclass(frozen=True)
class CashBalance:
    ordinal: int
    currency: str
    name: str
    base_value: Decimal
    weight: Decimal


@dataclass(frozen=True)
class CurrencyContract:
    ordinal: int
    share_class_code: str | None
    name: str
    currency: str | None
    delivery_date: date | None
    bought_sold: str | None
    amount: Decimal | None
    current_value: Decimal | None
    unrealised_gain_loss: Decimal | None
    nav_impact: Decimal | None
    long_short: str | None


@dataclass(frozen=True)
class Workbook:
    fund_code: str
    fund_name: str
    as_of_date: date
    share_classes: tuple[ShareClass, ...]
    positions: tuple[Position, ...]
    nav_history: tuple[NavPoint, ...]
    cash_balances: tuple[CashBalance, ...]
    currency_contracts: tuple[CurrencyContract, ...]


def normalise_isin(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def valid_isin(value: str) -> bool:
    """Validate the ISO 6166 shape and Luhn check digit."""
    if not _ISIN_RE.fullmatch(value):
        return False
    digits = "".join(str(int(ch, 36)) if ch.isalpha() else ch for ch in value)
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        number = int(char)
        if index % 2 == parity:
            number *= 2
        total += number // 10 + number % 10
    return total % 10 == 0


def _date(value: object, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    raise WorkbookError(f"{label} is not a valid date")


def _decimal(value: object, label: str, *, required: bool = False) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise WorkbookError(f"{label} is required")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WorkbookError(f"{label} is not numeric") from exc
    if not math.isfinite(float(result)):
        raise WorkbookError(f"{label} is not finite")
    return result


def _clean(value: object) -> str | None:
    cleaned = str(value).strip() if value not in (None, "") else ""
    return cleaned or None


def _optional_date(value: object, label: str) -> date | None:
    return None if value in (None, "") else _date(value, label)


def _parse_nav_history(book: Any, fund_code: str) -> tuple[NavPoint, ...]:
    if "DailyNAV" not in book.sheetnames:
        return ()
    sheet = book["DailyNAV"]
    points: dict[tuple[date, str], NavPoint] = {}
    for row_number in range(9, sheet.max_row + 1):
        if _clean(sheet.cell(row_number, 1).value) != fund_code:
            continue
        class_code = _clean(sheet.cell(row_number, 2).value)
        raw_date = sheet.cell(row_number, 4).value
        nav = _decimal(
            sheet.cell(row_number, 15).value
            or sheet.cell(row_number, 16).value
            or sheet.cell(row_number, 17).value
            or sheet.cell(row_number, 13).value
            or sheet.cell(row_number, 11).value,
            f"DailyNAV row {row_number} NAV",
        )
        if not class_code or raw_date in (None, "") or nav is None or nav <= 0:
            continue
        point_date = _date(raw_date, f"DailyNAV row {row_number} date")
        points[(point_date, class_code)] = NavPoint(
            date=point_date,
            class_code=class_code,
            class_name=_clean(sheet.cell(row_number, 3).value),
            nav=nav,
            units_outstanding=_decimal(
                sheet.cell(row_number, 6).value,
                f"DailyNAV row {row_number} units outstanding",
            ),
            fund_value=_decimal(
                sheet.cell(row_number, 7).value,
                f"DailyNAV row {row_number} fund value",
            ),
        )
    return tuple(points[key] for key in sorted(points))


def _parse_cash_balances(book: Any) -> tuple[CashBalance, ...]:
    if "Cash_at_Bank" not in book.sheetnames:
        return ()
    sheet = book["Cash_at_Bank"]
    balances: list[CashBalance] = []
    for row_number in range(12, sheet.max_row + 1):
        currency = _clean(sheet.cell(row_number, 1).value)
        name = _clean(sheet.cell(row_number, 3).value)
        base_value = _decimal(
            sheet.cell(row_number, 10).value,
            f"Cash_at_Bank row {row_number} base cash",
        )
        weight = _decimal(
            sheet.cell(row_number, 16).value,
            f"Cash_at_Bank row {row_number} NAV weighting",
        )
        if not currency or not name or name.upper() == "TOTAL" or base_value is None:
            continue
        balances.append(
            CashBalance(
                ordinal=len(balances) + 1,
                currency=currency,
                name=name,
                base_value=base_value,
                weight=weight or Decimal(),
            )
        )
    return tuple(balances)


def _parse_currency_contracts(book: Any) -> tuple[CurrencyContract, ...]:
    if "Open_Currency_Contracts" not in book.sheetnames:
        return ()
    sheet = book["Open_Currency_Contracts"]
    contracts: list[CurrencyContract] = []
    for row_number in range(12, sheet.max_row + 1):
        name = _clean(sheet.cell(row_number, 4).value)
        if not name:
            continue
        contracts.append(
            CurrencyContract(
                ordinal=len(contracts) + 1,
                share_class_code=_clean(sheet.cell(row_number, 3).value),
                name=name,
                currency=_clean(sheet.cell(row_number, 5).value),
                delivery_date=_optional_date(
                    sheet.cell(row_number, 9).value,
                    f"Open_Currency_Contracts row {row_number} delivery date",
                ),
                bought_sold={
                    "B": "Bought",
                    "S": "Sold",
                }.get(
                    _clean(sheet.cell(row_number, 11).value) or "",
                    _clean(sheet.cell(row_number, 11).value),
                ),
                amount=_decimal(
                    sheet.cell(row_number, 14).value,
                    f"Open_Currency_Contracts row {row_number} amount",
                ),
                current_value=_decimal(
                    sheet.cell(row_number, 17).value,
                    f"Open_Currency_Contracts row {row_number} current value",
                ),
                unrealised_gain_loss=_decimal(
                    sheet.cell(row_number, 20).value,
                    f"Open_Currency_Contracts row {row_number} unrealised gain/loss",
                ),
                nav_impact=_decimal(
                    sheet.cell(row_number, 23).value,
                    f"Open_Currency_Contracts row {row_number} NAV impact",
                ),
                long_short={
                    "L": "Long",
                    "S": "Short",
                }.get(
                    _clean(sheet.cell(row_number, 22).value) or "",
                    _clean(sheet.cell(row_number, 22).value),
                ),
            )
        )
    return tuple(contracts)


def _fund_name(names: list[str]) -> str:
    prefix = names[0]
    for name in names[1:]:
        while prefix and not name.startswith(prefix):
            prefix = prefix[:-1]
    candidate = prefix.rstrip(" -")
    candidate = re.sub(
        r"\s+(?:[A-Z]|EUR(?:\s+Hedged)?|USD(?:\s+Hedged)?)$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    if len(candidate) < 8:
        # A future report may carry only one class; remove the verified class suffixes.
        candidate = re.sub(
            r"\s+(?:[A-Z]\s+)?(?:EUR|USD)?\s*(?:Hedged\s+)?(?:Acc|Inc)$",
            "",
            names[0],
            flags=re.IGNORECASE,
        ).strip()
    if not candidate:
        raise WorkbookError("Could not derive the fund name from its share classes")
    return candidate


def parse_workbook(content: bytes) -> Workbook:
    if not content:
        raise WorkbookError("The workbook is empty")
    if len(content) > MAX_WORKBOOK_BYTES:
        raise WorkbookError("The workbook exceeds the 25 MiB limit")
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_WORKBOOK_UNCOMPRESSED_BYTES:
                raise WorkbookError("The expanded workbook exceeds the 100 MiB limit")
    except BadZipFile as exc:
        raise WorkbookError("The file is not a readable XLSX workbook") from exc
    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, OSError, ValueError, KeyError) as exc:
        raise WorkbookError("The file is not a readable XLSX workbook") from exc
    required = {"IOControl", "Valuation"}
    missing = required.difference(book.sheetnames)
    if missing:
        raise WorkbookError(f"Missing required sheet(s): {', '.join(sorted(missing))}")

    control = book["IOControl"]
    valuation = book["Valuation"]
    fund_code = _clean(control["E2"].value) or _clean(control["B5"].value)
    if not fund_code:
        raise WorkbookError("The fund code is missing")
    control_date = _date(control["E3"].value or control["B7"].value, "IOControl report date")
    valuation_date = _date(valuation["A7"].value, "Valuation report date")
    if control_date != valuation_date:
        raise WorkbookError("IOControl and Valuation report dates do not agree")

    classes: list[ShareClass] = []
    for row_number in range(3, control.max_row + 1):
        account = _clean(control.cell(row_number, 27).value)
        code = _clean(control.cell(row_number, 28).value)
        name = _clean(control.cell(row_number, 29).value)
        raw_isin = _clean(control.cell(row_number, 30).value)
        if account != fund_code or not code or not name or not raw_isin:
            continue
        isin = normalise_isin(raw_isin)
        if not valid_isin(isin):
            raise WorkbookError(f"IOControl row {row_number} has an invalid ISIN")
        classes.append(
            ShareClass(
                code=code,
                name=name,
                isin=isin,
                sedol=_clean(control.cell(row_number, 31).value),
            )
        )
    if not classes:
        raise WorkbookError("No share classes were found in IOControl")
    if len({item.isin for item in classes}) != len(classes):
        raise WorkbookError("IOControl contains a duplicate share-class ISIN")

    positions: list[Position] = []
    for row_number in range(12, valuation.max_row + 1):
        name = _clean(valuation.cell(row_number, 4).value)
        if not name or name.upper().startswith("TOTAL VALUE"):
            continue
        value = _decimal(
            valuation.cell(row_number, 20).value or valuation.cell(row_number, 16).value,
            f"Valuation row {row_number} market value",
            required=True,
        )
        weight = _decimal(
            valuation.cell(row_number, 23).value,
            f"Valuation row {row_number} weight",
            required=True,
        )
        if value is None or weight is None:  # required=True above; keeps the narrowing explicit
            raise WorkbookError(f"Valuation row {row_number} is incomplete")
        if weight < 0:
            raise WorkbookError(f"Valuation row {row_number} has a negative weight")
        raw_isin = normalise_isin(valuation.cell(row_number, 2).value)
        if raw_isin and not valid_isin(raw_isin):
            raise WorkbookError(f"Valuation row {row_number} has an invalid ISIN")
        asset_group = _clean(valuation.cell(row_number, 18).value)
        positions.append(
            Position(
                ordinal=len(positions) + 1,
                isin=raw_isin or None,
                sedol=_clean(valuation.cell(row_number, 1).value),
                name=name,
                long_name=_clean(
                    valuation.cell(row_number, 7).value or valuation.cell(row_number, 5).value
                ),
                currency=_clean(valuation.cell(row_number, 10).value),
                asset_group=asset_group,
                asset_class=_ASSET_CLASSES.get(asset_group or "", "Other"),
                country_name=_clean(valuation.cell(row_number, 24).value),
                country_code=_clean(valuation.cell(row_number, 25).value),
                maturity_date=(
                    _date(
                        valuation.cell(row_number, 9).value, f"Valuation row {row_number} maturity"
                    )
                    if valuation.cell(row_number, 9).value not in (None, "")
                    else None
                ),
                quantity=_decimal(
                    valuation.cell(row_number, 12).value, f"Valuation row {row_number} quantity"
                ),
                price=_decimal(
                    valuation.cell(row_number, 13).value, f"Valuation row {row_number} price"
                ),
                prior_price=_decimal(
                    valuation.cell(row_number, 21).value,
                    f"Valuation row {row_number} prior price",
                ),
                movement_percent=_decimal(
                    valuation.cell(row_number, 22).value,
                    f"Valuation row {row_number} movement",
                ),
                book_cost=_decimal(
                    valuation.cell(row_number, 14).value, f"Valuation row {row_number} book cost"
                ),
                market_value=value,
                weight=weight,
            )
        )
    if not positions:
        raise WorkbookError("No constituent positions were found in Valuation")

    return Workbook(
        fund_code=fund_code,
        fund_name=_fund_name([item.name for item in classes]),
        as_of_date=control_date,
        share_classes=tuple(classes),
        positions=tuple(positions),
        nav_history=_parse_nav_history(book, fund_code),
        cash_balances=_parse_cash_balances(book),
        currency_contracts=_parse_currency_contracts(book),
    )


async def import_workbook(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    imported_by: str | None,
) -> dict[str, Any]:
    parsed = parse_workbook(content)
    digest = hashlib.sha256(content).hexdigest()
    existing = (
        (
            await session.execute(
                text(
                    """SELECT s.id, s.uid, f.uid AS fund_uid, f.name, s.as_of_date
                   FROM lookthrough.snapshots s
                   JOIN lookthrough.funds f ON f.id = s.fund_id
                  WHERE s.file_sha256 = :digest"""
                ),
                {"digest": digest},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        enriched = await _enrich_existing_snapshot(session, int(existing["id"]), parsed)
        return {
            "filename": basename(filename),
            "status": "enriched" if enriched else "unchanged",
            "fund_uid": existing["fund_uid"],
            "fund_name": existing["name"],
            "as_of_date": existing["as_of_date"],
            "share_class_count": len(parsed.share_classes),
            "position_count": len(parsed.positions),
        }

    fund = (
        (
            await session.execute(
                text(
                    """INSERT INTO lookthrough.funds (uid, fund_code, name)
                   VALUES (:uid, :code, :name)
                   ON CONFLICT (source, fund_code) DO UPDATE
                     SET name = EXCLUDED.name, updated_at = now()
                   RETURNING id, uid"""
                ),
                {"uid": new_id(), "code": parsed.fund_code, "name": parsed.fund_name},
            )
        )
        .mappings()
        .one()
    )

    for share_class in parsed.share_classes:
        collision = (
            await session.execute(
                text(
                    """SELECT f.fund_code
                         FROM lookthrough.share_classes sc
                         JOIN lookthrough.funds f ON f.id = sc.fund_id
                        WHERE sc.isin = :isin
                          AND (sc.fund_id <> :fund_id OR sc.class_code <> :class_code)"""
                ),
                {
                    "isin": share_class.isin,
                    "fund_id": fund["id"],
                    "class_code": share_class.code,
                },
            )
        ).scalar_one_or_none()
        if collision:
            raise WorkbookError(
                f"Share-class ISIN {share_class.isin} is already assigned to fund {collision}"
            )
        await session.execute(
            text(
                """INSERT INTO lookthrough.share_classes
                     (fund_id, class_code, name, isin, sedol)
                   VALUES (:fund_id, :code, :name, :isin, :sedol)
                   ON CONFLICT (fund_id, class_code) DO UPDATE SET
                     name = EXCLUDED.name, isin = EXCLUDED.isin, sedol = EXCLUDED.sedol,
                     updated_at = now()"""
            ),
            {
                "fund_id": fund["id"],
                "code": share_class.code,
                "name": share_class.name,
                "isin": share_class.isin,
                "sedol": share_class.sedol,
            },
        )

    supersedes = (
        (
            await session.execute(
                text(
                    """SELECT id, uid FROM lookthrough.snapshots
                    WHERE fund_id = :fund_id AND as_of_date = :as_of
                    ORDER BY imported_at DESC, id DESC LIMIT 1"""
                ),
                {"fund_id": fund["id"], "as_of": parsed.as_of_date},
            )
        )
        .mappings()
        .one_or_none()
    )
    snapshot_uid = new_id()
    coverage = sum((item.weight for item in parsed.positions), Decimal())
    snapshot_id = (
        await session.execute(
            text(
                """INSERT INTO lookthrough.snapshots
                     (uid, fund_id, as_of_date, filename, file_sha256, imported_by,
                      supersedes_id, position_count, share_class_count, coverage)
                   VALUES
                     (:uid, :fund_id, :as_of, :filename, :digest, :actor,
                      :supersedes, :positions, :classes, :coverage)
                   RETURNING id"""
            ),
            {
                "uid": snapshot_uid,
                "fund_id": fund["id"],
                "as_of": parsed.as_of_date,
                "filename": basename(filename),
                "digest": digest,
                "actor": imported_by,
                "supersedes": supersedes["id"] if supersedes else None,
                "positions": len(parsed.positions),
                "classes": len(parsed.share_classes),
                "coverage": coverage,
            },
        )
    ).scalar_one()
    for position in parsed.positions:
        await session.execute(
            text(
                """INSERT INTO lookthrough.positions
                     (snapshot_id, ordinal, isin, sedol, name, long_name, currency,
                      asset_group, asset_class, country_code, country_name, maturity_date,
                      quantity, price, prior_price, movement_percent, book_cost,
                      market_value, weight)
                   VALUES
                     (:snapshot_id, :ordinal, :isin, :sedol, :name, :long_name, :currency,
                      :asset_group, :asset_class, :country_code, :country_name, :maturity_date,
                      :quantity, :price, :prior_price, :movement_percent, :book_cost,
                      :market_value, :weight)"""
            ),
            {"snapshot_id": snapshot_id, **position.__dict__},
        )
    for point in parsed.nav_history:
        await session.execute(
            text(
                """INSERT INTO lookthrough.nav_history
                     (snapshot_id, date, class_code, class_name, nav,
                      units_outstanding, fund_value)
                   VALUES
                     (:snapshot_id, :date, :class_code, :class_name, :nav,
                      :units_outstanding, :fund_value)"""
            ),
            {"snapshot_id": snapshot_id, **point.__dict__},
        )
    for balance in parsed.cash_balances:
        await session.execute(
            text(
                """INSERT INTO lookthrough.cash_balances
                     (snapshot_id, ordinal, currency, name, base_value, weight)
                   VALUES
                     (:snapshot_id, :ordinal, :currency, :name, :base_value, :weight)"""
            ),
            {"snapshot_id": snapshot_id, **balance.__dict__},
        )
    for contract in parsed.currency_contracts:
        await session.execute(
            text(
                """INSERT INTO lookthrough.currency_contracts
                     (snapshot_id, ordinal, share_class_code, name, currency, delivery_date,
                      bought_sold, amount, current_value, unrealised_gain_loss,
                      nav_impact, long_short)
                   VALUES
                     (:snapshot_id, :ordinal, :share_class_code, :name, :currency, :delivery_date,
                      :bought_sold, :amount, :current_value, :unrealised_gain_loss,
                      :nav_impact, :long_short)"""
            ),
            {"snapshot_id": snapshot_id, **contract.__dict__},
        )
    return {
        "filename": basename(filename),
        "status": "corrected" if supersedes else "imported",
        "fund_uid": fund["uid"],
        "fund_name": parsed.fund_name,
        "as_of_date": parsed.as_of_date,
        "share_class_count": len(parsed.share_classes),
        "position_count": len(parsed.positions),
    }


async def _enrich_existing_snapshot(
    session: AsyncSession, snapshot_id: int, parsed: Workbook
) -> bool:
    """Fill analytics added after the immutable source snapshot was first imported."""
    counts = (
        (
            await session.execute(
                text(
                    """SELECT
                     (SELECT count(*) FROM lookthrough.nav_history
                       WHERE snapshot_id = :snapshot) AS nav,
                     (SELECT count(*) FROM lookthrough.cash_balances
                       WHERE snapshot_id = :snapshot) AS cash,
                     (SELECT count(*) FROM lookthrough.currency_contracts
                       WHERE snapshot_id = :snapshot) AS contracts,
                     (SELECT count(*) FROM lookthrough.positions
                       WHERE snapshot_id = :snapshot
                         AND (prior_price IS NOT NULL
                              OR movement_percent IS NOT NULL)) AS movements"""
                ),
                {"snapshot": snapshot_id},
            )
        )
        .mappings()
        .one()
    )
    needs_nav = bool(parsed.nav_history) and not counts["nav"]
    needs_cash = bool(parsed.cash_balances) and not counts["cash"]
    needs_contracts = bool(parsed.currency_contracts) and not counts["contracts"]
    needs_movements = (
        any(
            row.prior_price is not None or row.movement_percent is not None
            for row in parsed.positions
        )
        and not counts["movements"]
    )
    if not any((needs_nav, needs_cash, needs_contracts, needs_movements)):
        return False

    if needs_movements:
        for position in parsed.positions:
            await session.execute(
                text(
                    """UPDATE lookthrough.positions
                          SET prior_price = :prior_price,
                              movement_percent = :movement_percent
                        WHERE snapshot_id = :snapshot_id AND ordinal = :ordinal"""
                ),
                {
                    "snapshot_id": snapshot_id,
                    "ordinal": position.ordinal,
                    "prior_price": position.prior_price,
                    "movement_percent": position.movement_percent,
                },
            )
    if needs_nav:
        for point in parsed.nav_history:
            await session.execute(
                text(
                    """INSERT INTO lookthrough.nav_history
                         (snapshot_id, date, class_code, class_name, nav,
                          units_outstanding, fund_value)
                       VALUES
                         (:snapshot_id, :date, :class_code, :class_name, :nav,
                          :units_outstanding, :fund_value)
                       ON CONFLICT (snapshot_id, date, class_code) DO NOTHING"""
                ),
                {"snapshot_id": snapshot_id, **point.__dict__},
            )
    if needs_cash:
        for balance in parsed.cash_balances:
            await session.execute(
                text(
                    """INSERT INTO lookthrough.cash_balances
                         (snapshot_id, ordinal, currency, name, base_value, weight)
                       VALUES
                         (:snapshot_id, :ordinal, :currency, :name, :base_value, :weight)
                       ON CONFLICT (snapshot_id, ordinal) DO NOTHING"""
                ),
                {"snapshot_id": snapshot_id, **balance.__dict__},
            )
    if needs_contracts:
        for contract in parsed.currency_contracts:
            await session.execute(
                text(
                    """INSERT INTO lookthrough.currency_contracts
                         (snapshot_id, ordinal, share_class_code, name, currency, delivery_date,
                          bought_sold, amount, current_value, unrealised_gain_loss,
                          nav_impact, long_short)
                       VALUES
                         (:snapshot_id, :ordinal, :share_class_code, :name, :currency,
                          :delivery_date,
                          :bought_sold, :amount, :current_value, :unrealised_gain_loss,
                          :nav_impact, :long_short)
                       ON CONFLICT (snapshot_id, ordinal) DO NOTHING"""
                ),
                {"snapshot_id": snapshot_id, **contract.__dict__},
            )
    return True
