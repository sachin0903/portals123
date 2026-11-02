"""The EMT cost-and-charge extract, collapsed to one current charge per ISIN.

SEI's reporting UI exports every effective-dated snapshot in the window. A typical
ISIN appears several times, once per file-creation date. The charge a firm
discloses is the latest snapshot: that date is when the file was published, and
the ongoing-charge dates are when the figure itself took effect.

``ocf`` is the actual ongoing-charge percentage on that snapshot, or the estimate
when the snapshot published no actual. A zero is a published figure. Amounts are
stored as they were printed, not rescaled.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pyarrow as pa

_SCALE = Decimal("0.0000000001")

#: Column order of ``data/emt_charges.csv`` and of ``platform.emt_charges``.
COLUMNS: tuple[str, ...] = (
    "isin",
    "sedol",
    "file_created_on",
    "ongoing_charge_estimate",
    "ongoing_charge_estimate_on",
    "ongoing_charge_actual",
    "ongoing_charge_actual_on",
    "transaction_fee_estimate",
    "transaction_fee_estimate_on",
    "transaction_fee_actual",
    "transaction_fee_actual_on",
    "distribution_fee_estimate",
    "distribution_fee_estimate_on",
    "distribution_fee_actual",
    "distribution_fee_actual_on",
    "management_fee_estimate",
    "management_fee_estimate_on",
    "management_fee_actual",
    "management_fee_actual_on",
    "ocf",
)

_AMOUNTS = frozenset(
    {
        "ongoing_charge_estimate",
        "ongoing_charge_actual",
        "transaction_fee_estimate",
        "transaction_fee_actual",
        "distribution_fee_estimate",
        "distribution_fee_actual",
        "management_fee_estimate",
        "management_fee_actual",
        "ocf",
    }
)

SCHEMA: dict[str, pa.DataType] = {
    name: pa.string()
    if name in {"isin", "sedol"}
    else pa.date32()
    if name.endswith("_on")
    else pa.decimal128(18, 10)
    for name in COLUMNS
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        number = value
    else:
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            return None
    return number.quantize(_SCALE)


def _day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _rank(row: dict[str, Any]) -> tuple[date, date, date]:
    """Latest publication first. Charge dates only break a tie, which the measured
    file does not have — every ISIN's latest file-creation date is unique."""
    return (
        _day(row.get("file_created_on")) or date.min,
        _day(row.get("ongoing_charge_actual_on")) or date.min,
        _day(row.get("ongoing_charge_estimate_on")) or date.min,
    )


def collapse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per ISIN: the latest file-creation date, then its ongoing charge."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        isin = _text(row.get("isin"))
        if isin is None:
            continue
        grouped[isin].append(row)

    published: list[dict[str, Any]] = []
    for isin, group in grouped.items():
        winner = max(group, key=_rank)
        actual = _decimal(winner.get("ongoing_charge_actual"))
        estimate = _decimal(winner.get("ongoing_charge_estimate"))
        charge: dict[str, Any] = {"isin": isin, "sedol": _text(winner.get("sedol"))}
        for column in COLUMNS:
            if column in {"isin", "sedol", "ocf"}:
                continue
            if column.endswith("_on"):
                charge[column] = _day(winner.get(column))
            else:
                charge[column] = _decimal(winner.get(column))
        charge["ocf"] = actual if actual is not None else estimate
        published.append(charge)
    published.sort(key=lambda row: str(row["isin"]))
    return published


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        text = format(value.quantize(_SCALE), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_cell(row.get(column)) for column in COLUMNS})


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def to_table(rows: list[dict[str, Any]]) -> pa.Table:
    columns: dict[str, list[Any]] = {name: [] for name in COLUMNS}
    for row in rows:
        for name in COLUMNS:
            columns[name].append(row.get(name))
    return pa.table({name: pa.array(values, type=SCHEMA[name]) for name, values in columns.items()})


def publish(path: Path, *, expected: str | None) -> pa.Table:
    """Read a charge file, collapse it, and return the warehouse table.

    ``expected`` is the sha256 written into the model function. A file that does
    not match has been replaced without regenerating the model, so the build
    stops rather than publishing a charge the planner did not see.
    """
    if expected is not None and digest(path) != expected:
        raise RuntimeError(
            "EMT charge file does not match this model. "
            "Regenerate it with scripts/build_emt_charges.py."
        )
    return to_table(collapse(read_csv(path)))
