"""Run one shipped report under the caller's own row-level security.

SQL reports go through the dataset envelope (read-only, timeout, identity re-check) with this
module's own row cap. A preview may say it is only the first rows. An export that would pass
the cap is refused and no file is built from the partial result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.lake.holdings import HoldingsUnavailable
from app.render.resolve import _execute_scoped
from app.reports import cash
from app.reports.catalogue import Report, get

PREVIEW_LIMIT = 100
REPORT_ROW_LIMIT = 50_000
REPORT_TIMEOUT_MS = 30_000


class ReportNotFound(Exception):
    """No such report, or it is switched off. The two are the same answer to a firm."""

    detail = "That report is not available."


class ReportTooLarge(Exception):
    """The export passed the cap. The rows are not returned."""

    def __init__(self, limit: int) -> None:
        self.detail = (
            f"This report has more than {limit:,} rows, so no file was written. "
            "Narrow the dates and try again."
        )


class ReportUnavailable(Exception):
    """The holdings slice could not be read, or no firm is in scope. Nothing is written."""

    def __init__(
        self, detail: str = "This firm's holdings could not be read, so nothing was written."
    ) -> None:
        self.detail = detail


class ReportInput(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Result:
    report: Report
    rows: list[dict[str, Any]]
    truncated: bool
    #: The caller cannot see the whole firm, so the rows are their book.
    narrowed: bool


def binds_for(report: Report, raw: dict[str, Any]) -> dict[str, Any]:
    """The statement's binds, one per declared parameter. Missing optional values are NULL."""
    binds: dict[str, Any] = {}
    for param in report.params:
        value = raw.get(param.key)
        if value is None or value == "":
            if param.required:
                raise ReportInput(f"{param.label} is required.")
            binds[param.key] = None
            continue
        if param.kind == "date":
            if isinstance(value, datetime):
                binds[param.key] = value.date()
            elif isinstance(value, date):
                binds[param.key] = value
            else:
                try:
                    binds[param.key] = date.fromisoformat(str(value))
                except ValueError as exc:
                    raise ReportInput(f"{param.label} is not a date.") from exc
        elif str(value) not in param.options:
            raise ReportInput(f"{param.label} is not one of the report's choices.")
        else:
            binds[param.key] = str(value)
    start, end = binds.get("from_date"), binds.get("to_date")
    if isinstance(start, date) and isinstance(end, date) and start > end:
        raise ReportInput("The start date is after the end date.")
    return binds


def present(report: Report, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSON-safe cells in catalogue order. Money stays a number so a spreadsheet can sum it."""
    presented: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for column in report.columns:
            item[column.key] = _cell(row.get(column.key), column.kind)
        presented.append(item)
    return presented


def _cell(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text_value = str(value)
        return text_value[:10]
    if kind in ("money", "number", "percent"):
        return float(Decimal(str(value)))
    return value if isinstance(value, str) else str(value)


async def enabled_codes(session: AsyncSession) -> set[str]:
    rows = await session.execute(text("SELECT code FROM platform.mi_reports WHERE enabled"))
    return {str(code) for (code,) in rows}


async def execute(
    session: AsyncSession,
    report: Report,
    raw: dict[str, Any],
    *,
    preview: bool,
    tenant_id: int | None = None,
    row_limit: int | None = None,
) -> Result:
    """Run ``report`` as the session's principal.

    ``tenant_id`` is only needed when the session has no claims yet (a test, or a worker).
    A request session already carries them, and the envelope keeps those rather than minting
    a wider set.
    """
    limit = row_limit if row_limit is not None else PREVIEW_LIMIT if preview else REPORT_ROW_LIMIT
    if report.reads_lake:
        return await _execute_cash(session, report, preview=preview, limit=limit)
    firm = tenant_id if tenant_id is not None else await _session_tenant(session)
    run = await _execute_scoped(
        session,
        report.sql or "",
        binds_for(report, raw),
        tenant_id=firm,
        subject_id=None,
        scope="global",
        row_limit=limit,
        timeout_ms=REPORT_TIMEOUT_MS,
    )
    if run.truncated and not preview:
        raise ReportTooLarge(limit)
    return Result(report, present(report, run.rows), run.truncated, run.narrowed)


async def run(
    session: AsyncSession,
    code: str,
    raw: dict[str, Any],
    *,
    preview: bool,
    tenant_id: int | None = None,
) -> Result:
    """``execute`` after the global switch. A code that is off looks the same as an unknown one."""
    report = get(code)
    if report is None or code not in await enabled_codes(session):
        raise ReportNotFound()
    return await execute(session, report, raw, preview=preview, tenant_id=tenant_id)


async def _execute_cash(
    session: AsyncSession, report: Report, *, preview: bool, limit: int
) -> Result:
    try:
        rows = await cash.gather(session)
    except HoldingsUnavailable as exc:
        raise ReportUnavailable() from exc
    truncated = len(rows) > limit
    if truncated and not preview:
        raise ReportTooLarge(limit)
    narrowed = not bool((await session.execute(text("SELECT auth.can_read_all()"))).scalar())
    return Result(report, present(report, rows[:limit]), truncated, narrowed)


async def _session_tenant(session: AsyncSession) -> int:
    found = (await session.execute(text("SELECT auth.tenant_id()"))).scalar()
    if found is None:
        raise ReportUnavailable("No firm is in scope for this report.")
    return int(found)
