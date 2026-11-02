"""Turn an EMT Cost and Charge Effective Dates workbook into the pipeline CSV.

The workbook is SEI's reporting-UI export: a title banner, then one row per
ISIN per file-creation date. This writes ``data/emt_charges.csv`` with one
current charge per ISIN and stamps the sha256 into ``models/emt_charges.py``.
That literal sits inside the model function because interlace fingerprints only
the function source — a new file that left it unchanged would never rebuild.

    uv run --project services/sei --with openpyxl \\
        python scripts/build_emt_charges.py \\
        "~/Downloads/EMT Cost and Charge Effective Dates Report.xlsx"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from sei_sync.emt import collapse, digest, write_csv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "emt_charges.csv"
MODEL = ROOT / "models" / "emt_charges.py"
_DIGEST = re.compile(r'^( {4}digest = ")[0-9a-f]{64}(")\s*$', re.MULTILINE)

# Re-exported names the workbook actually prints. "Estimated" and "Estimate"
# are both used; the mapping follows the sheet, not a tidied vocabulary.
_HEADERS: dict[str, str] = {
    "File Creation Date": "file_created_on",
    "ISIN": "isin",
    "SEDOL": "sedol",
    "Ongoing Charge Estimate Percentage": "ongoing_charge_estimate",
    "Ongoing Charge Estimate Date": "ongoing_charge_estimate_on",
    "Ongoing Charge Actual Percentage": "ongoing_charge_actual",
    "Ongoing Charge Actual Date": "ongoing_charge_actual_on",
    "Transaction Fee Estimate Amount": "transaction_fee_estimate",
    "Transaction Fee Estimate Date": "transaction_fee_estimate_on",
    "Transaction Fee Actual Amount": "transaction_fee_actual",
    "Transaction Fee Actual Date": "transaction_fee_actual_on",
    "Distribution Fee Estimated Amount": "distribution_fee_estimate",
    "Distribution Fee Estimated Amount Effective Date": "distribution_fee_estimate_on",
    "Distribution Fee Actual Amount": "distribution_fee_actual",
    "Distribution Fee Actual Amount Effective Date": "distribution_fee_actual_on",
    "Management Fee Estimated Amount": "management_fee_estimate",
    "Management Fee Estimated Amount Effective Date": "management_fee_estimate_on",
    "Management Fee Actual Amount": "management_fee_actual",
    "Management Fee Actual Amount Effective Date": "management_fee_actual_on",
}


def read_xlsx(path: Path) -> list[dict[str, Any]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            raise SystemExit(f"{path} has no sheet")
        indexes: dict[int, str] = {}
        found = False
        mapped: list[dict[str, Any]] = []
        for raw in sheet.iter_rows(values_only=True):
            if not found:
                labels = [str(cell).strip() if cell is not None else "" for cell in raw]
                if "ISIN" not in labels or "File Creation Date" not in labels:
                    continue
                missing = [name for name in _HEADERS if name not in labels]
                if missing:
                    raise SystemExit(f"EMT report is missing columns: {', '.join(missing)}")
                indexes = {labels.index(name): key for name, key in _HEADERS.items()}
                found = True
                continue
            if not raw or all(cell is None for cell in raw):
                continue
            record = {
                key: raw[index] if index < len(raw) else None for index, key in indexes.items()
            }
            if not str(record.get("isin") or "").strip():
                continue
            mapped.append(record)
        if not found:
            raise SystemExit(f"{path} has no EMT header row")
        return mapped
    finally:
        workbook.close()


def stamp(path: Path, digest_hex: str) -> None:
    source = path.read_text(encoding="utf-8")
    updated, count = _DIGEST.subn(rf"\g<1>{digest_hex}\2", source, count=1)
    if count != 1:
        raise SystemExit(f"{path} has no digest literal for the EMT extract")
    path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args(argv)
    source = args.xlsx.expanduser()
    if not source.is_file():
        raise SystemExit(f"No workbook at {source}")
    charges = collapse(read_xlsx(source))
    write_csv(args.output, charges)
    stamp(MODEL, digest(args.output))
    print(f"{len(charges)} charges -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
