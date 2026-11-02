"""CSV and Excel for a report that has already been run.

The columns are the catalogue's, in its order. A number is a number in the workbook so it
can be summed; CSV carries the same value as text.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.reports.catalogue import Report


def render_csv(report: Report, rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column.label for column in report.columns])
    for row in rows:
        writer.writerow([_text(row.get(column.key)) for column in report.columns])
    return buffer.getvalue().encode("utf-8-sig")


def render_xlsx(report: Report, rows: list[dict[str, Any]]) -> bytes:
    book = Workbook()
    sheet = cast(Worksheet, book.active)
    sheet.title = "Report"
    sheet.append([column.label for column in report.columns])
    for row in rows:
        sheet.append([_excel(row.get(column.key), column.kind) for column in report.columns])
    for index, column in enumerate(report.columns, start=1):
        if column.kind == "money":
            _format_column(sheet, index, "#,##0.00")
        elif column.kind == "percent":
            _format_column(sheet, index, "0.00")
        elif column.kind == "date":
            _format_column(sheet, index, "DD MMM YYYY")
    raw = io.BytesIO()
    book.save(raw)
    return raw.getvalue()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _excel(value: Any, kind: str) -> Any:
    if value is None or value == "":
        return None
    if kind == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return value
    if kind == "date" and isinstance(value, datetime):
        return value.date()
    return value


def _format_column(sheet: Any, index: int, fmt: str) -> None:
    for cell in sheet.iter_cols(min_col=index, max_col=index, min_row=2):
        for item in cell:
            if item.value is not None:
                item.number_format = fmt
