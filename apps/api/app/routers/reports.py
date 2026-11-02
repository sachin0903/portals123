"""Management reports: operations switches a shipped query on, a firm runs it.

The query is the catalogue in :mod:`app.reports.catalogue`. This router does not accept SQL.
A firm session reads and downloads; only the operations session can change the switch.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, require_permission
from app.db import admin_session, tenant_session
from app.permissions import MI_REPORTS_READ, MI_REPORTS_WRITE, REPORTS_EXPORT, REPORTS_READ
from app.reports.catalogue import CATALOGUE, Report, get
from app.reports.files import render_csv, render_xlsx
from app.reports.runner import (
    ReportInput,
    ReportNotFound,
    ReportTooLarge,
    ReportUnavailable,
    enabled_codes,
    run,
)
from app.storage import content_disposition

platform = APIRouter(prefix="/reports", tags=["reports"])
admin = APIRouter(prefix="/operations/mi-reports", tags=["reports"])


class ReportParamOut(BaseModel):
    key: str
    label: str
    kind: str
    required: bool
    options: list[str]


class ReportColumnOut(BaseModel):
    key: str
    label: str
    kind: str


class ReportOut(BaseModel):
    code: str
    name: str
    description: str
    enabled: bool | None = None
    columns: list[ReportColumnOut]
    params: list[ReportParamOut]


class ReportPreviewOut(BaseModel):
    code: str
    name: str
    columns: list[ReportColumnOut]
    rows: list[dict[str, Any]]
    truncated: bool
    narrowed: bool


class ReportSwitch(BaseModel):
    enabled: bool


def _out(report: Report, *, enabled: bool | None = None) -> ReportOut:
    return ReportOut(
        code=report.code,
        name=report.name,
        description=report.description,
        enabled=enabled,
        columns=[
            ReportColumnOut(key=column.key, label=column.label, kind=column.kind)
            for column in report.columns
        ],
        params=[
            ReportParamOut(
                key=param.key,
                label=param.label,
                kind=param.kind,
                required=param.required,
                options=list(param.options),
            )
            for param in report.params
        ],
    )


def _params(
    report: Report,
    *,
    from_date: date | None,
    to_date: date | None,
    type_: str | None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if from_date is not None:
        raw["from_date"] = from_date
    if to_date is not None:
        raw["to_date"] = to_date
    if type_:
        raw["type"] = type_
    return raw


def _fail(exc: ReportNotFound | ReportTooLarge | ReportUnavailable | ReportInput) -> HTTPException:
    not_found = isinstance(exc, ReportNotFound)
    code = status.HTTP_404_NOT_FOUND if not_found else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=exc.detail)


@platform.get("", name="list-reports")
async def list_reports(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[ReportOut]:
    """Reports operations has switched on. A code that is off is not listed."""
    require_permission(principal, REPORTS_READ)
    codes = await enabled_codes(session)
    return [_out(report) for report in CATALOGUE if report.code in codes]


@platform.get("/{code}", name="get-report")
async def get_report(
    code: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ReportOut:
    require_permission(principal, REPORTS_READ)
    report = get(code)
    if report is None or code not in await enabled_codes(session):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ReportNotFound.detail)
    return _out(report)


@platform.get("/{code}/preview", name="preview-report")
async def preview_report(
    code: str,
    from_date: date | None = None,
    to_date: date | None = None,
    type: str | None = None,  # noqa: A002 - the query name the form sends
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ReportPreviewOut:
    """The first rows. ``truncated`` means the preview is not the whole extract."""
    require_permission(principal, REPORTS_READ)
    report = get(code)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ReportNotFound.detail)
    try:
        result = await run(
            session,
            code,
            _params(report, from_date=from_date, to_date=to_date, type_=type),
            preview=True,
        )
    except (ReportNotFound, ReportTooLarge, ReportUnavailable, ReportInput) as exc:
        raise _fail(exc) from exc
    return ReportPreviewOut(
        code=result.report.code,
        name=result.report.name,
        columns=[
            ReportColumnOut(key=column.key, label=column.label, kind=column.kind)
            for column in result.report.columns
        ],
        rows=result.rows,
        truncated=result.truncated,
        narrowed=result.narrowed,
    )


@platform.get("/{code}/export", name="export-report")
async def export_report(
    code: str,
    format: Literal["csv", "xlsx"] = "csv",  # noqa: A002 - the query name
    from_date: date | None = None,
    to_date: date | None = None,
    type: str | None = None,  # noqa: A002
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """The file. Over the row cap this is a 400 and an empty body, not a partial workbook."""
    require_permission(principal, REPORTS_EXPORT)
    report = get(code)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ReportNotFound.detail)
    try:
        result = await run(
            session,
            code,
            _params(report, from_date=from_date, to_date=to_date, type_=type),
            preview=False,
        )
    except (ReportNotFound, ReportTooLarge, ReportUnavailable, ReportInput) as exc:
        raise _fail(exc) from exc
    if format == "xlsx":
        body = render_xlsx(result.report, result.rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{result.report.code}.xlsx"
    else:
        body = render_csv(result.report, result.rows)
        media = "text/csv; charset=utf-8"
        filename = f"{result.report.code}.csv"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": content_disposition("attachment", filename)},
    )


@admin.get("", name="list-mi-reports")
async def list_mi_reports(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[ReportOut]:
    """Every shipped report, with the switch. A missing row is off."""
    require_permission(principal, MI_REPORTS_READ)
    codes = await enabled_codes(session)
    return [_out(report, enabled=report.code in codes) for report in CATALOGUE]


@admin.put("/{code}", name="set-mi-report")
async def set_mi_report(
    code: str,
    body: ReportSwitch,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> ReportOut:
    """Switch one shipped report on or off for every firm."""
    require_permission(principal, MI_REPORTS_WRITE)
    report = get(code)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report.")
    await session.execute(
        text(
            "INSERT INTO platform.mi_reports (code, enabled, updated_by) "
            "VALUES (:code, :enabled, auth.uid()) "
            "ON CONFLICT (code) DO UPDATE SET "
            "  enabled = EXCLUDED.enabled, updated_at = now(), updated_by = auth.uid()"
        ),
        {"code": code, "enabled": body.enabled},
    )
    return _out(report, enabled=body.enabled)
