"""Operations import and holder-gated reads for global fund look-through data."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import lookthrough
from app.auth import Principal, get_current_principal, require_permission
from app.db import admin_session, book_session
from app.permissions import LOOKTHROUGH_READ, LOOKTHROUGH_WRITE
from app.routers import crm
from app.routers.documents import CappedBodyRoute


class LookthroughUploadRoute(CappedBodyRoute):
    body_limit = lookthrough.MAX_WORKBOOK_BYTES * 10 + 1024 * 1024


router = APIRouter(tags=["look-through"], route_class=LookthroughUploadRoute)


@router.get("/admin/look-through", name="admin-list-look-through")
async def admin_list(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, object]:
    require_permission(principal, LOOKTHROUGH_READ)
    funds = _rows(
        await session.execute(
            text(
                """WITH active AS (
                     SELECT DISTINCT ON (fund_id, as_of_date)
                            id, fund_id, as_of_date, imported_at, position_count, coverage
                       FROM lookthrough.snapshots
                      ORDER BY fund_id, as_of_date, imported_at DESC, id DESC
                   ), latest AS (
                     SELECT DISTINCT ON (fund_id) *
                       FROM active
                      ORDER BY fund_id, as_of_date DESC, imported_at DESC, id DESC
                   )
                   SELECT f.uid, f.name, f.fund_code,
                          count(DISTINCT sc.id)::int AS share_class_count,
                          array_agg(DISTINCT sc.isin ORDER BY sc.isin)
                            FILTER (WHERE sc.isin IS NOT NULL) AS isins,
                          l.as_of_date AS latest_as_of,
                          l.position_count, l.coverage, l.imported_at
                     FROM lookthrough.funds f
                     LEFT JOIN lookthrough.share_classes sc ON sc.fund_id = f.id
                     LEFT JOIN latest l ON l.fund_id = f.id
                    GROUP BY f.id, l.as_of_date, l.position_count, l.coverage, l.imported_at
                    ORDER BY f.name"""
            )
        )
    )
    imports = _rows(
        await session.execute(
            text(
                """SELECT s.uid, f.uid AS fund_uid, f.name AS fund_name,
                          s.filename, s.as_of_date, s.position_count,
                          s.share_class_count, s.imported_at, old.uid AS supersedes_uid
                     FROM lookthrough.snapshots s
                     JOIN lookthrough.funds f ON f.id = s.fund_id
                     LEFT JOIN lookthrough.snapshots old ON old.id = s.supersedes_id
                    ORDER BY s.imported_at DESC, s.id DESC LIMIT 250"""
            )
        )
    )
    return {"funds": funds, "imports": imports}


@router.post("/admin/look-through/import", name="admin-import-look-through")
async def admin_import(
    files: list[UploadFile] = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, object]:
    require_permission(principal, LOOKTHROUGH_WRITE)
    if not files or len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Upload between one and ten XLSX workbooks.",
        )
    actor = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    results: list[dict[str, Any]] = []
    for file in files:
        filename = file.filename or "workbook.xlsx"
        if not filename.lower().endswith(".xlsx"):
            results.append(
                {
                    "filename": filename,
                    "status": "rejected",
                    "error": "Only XLSX files are accepted",
                }
            )
            continue
        if file.size is not None and file.size > lookthrough.MAX_WORKBOOK_BYTES:
            results.append(
                {
                    "filename": filename,
                    "status": "rejected",
                    "error": "The workbook exceeds the 25 MiB limit",
                }
            )
            continue
        content = await file.read(lookthrough.MAX_WORKBOOK_BYTES + 1)
        try:
            async with session.begin_nested():
                results.append(
                    await lookthrough.import_workbook(
                        session,
                        content=content,
                        filename=filename,
                        imported_by=str(actor) if actor else None,
                    )
                )
        except lookthrough.WorkbookError as exc:
            results.append({"filename": filename, "status": "rejected", "error": str(exc)})
    return {"results": results}


def _rows(result: Any) -> list[dict[str, Any]]:
    """Rows with every numeric as a float.

    These routes declare ``dict[str, object]``, so FastAPI serialises through pydantic, which
    renders a Decimal as a JSON *string*. Every reader types weight and coverage as numbers, and a
    string fails ``Number.isFinite`` silently: the register printed a raw 0.9934578303 and the
    treemap reported "no position weights are available" for a fully-populated snapshot.
    """
    return [
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in result.mappings().all()
    ]


def _breakdown(positions: list[dict[str, Any]], key: str, fallback: str) -> list[dict[str, object]]:
    grouped: dict[str, float] = defaultdict(float)
    for row in positions:
        grouped[str(row.get(key) or fallback)] += float(row["weight"])
    return [
        {"label": label, "value": round(value, 8)}
        for label, value in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


async def _fund_classes(session: AsyncSession, fund_uid: str) -> list[dict[str, Any]]:
    return _rows(
        await session.execute(
            text(
                """SELECT f.id AS fund_id, f.uid AS fund_uid, f.name AS fund_name,
                          f.fund_code, f.source, sc.class_code,
                          sc.name, sc.isin, sc.sedol
                     FROM lookthrough.funds f
                     JOIN lookthrough.share_classes sc ON sc.fund_id = f.id
                    WHERE f.uid = :uid
                    ORDER BY sc.name, sc.isin"""
            ),
            {"uid": fund_uid},
        )
    )


async def _fund_snapshots(session: AsyncSession, fund_id: int) -> list[dict[str, Any]]:
    return _rows(
        await session.execute(
            text(
                """SELECT DISTINCT ON (as_of_date)
                          id, uid, as_of_date, coverage, position_count
                     FROM lookthrough.snapshots
                    WHERE fund_id = :fund_id
                    ORDER BY as_of_date DESC, imported_at DESC, id DESC"""
            ),
            {"fund_id": fund_id},
        )
    )


async def _snapshot_positions(session: AsyncSession, snapshot_id: int) -> list[dict[str, Any]]:
    return _rows(
        await session.execute(
            text(
                """SELECT ordinal, isin, sedol, name, long_name, currency,
                          asset_group, asset_class, country_code, country_name,
                          maturity_date, quantity, price, prior_price, movement_percent,
                          book_cost, market_value, weight
                     FROM lookthrough.positions
                    WHERE snapshot_id = :snapshot
                    ORDER BY weight DESC, ordinal"""
            ),
            {"snapshot": snapshot_id},
        )
    )


async def _snapshot_analytics(
    session: AsyncSession,
    *,
    snapshot_id: int,
    share_class_code: str,
    holder_view: bool,
) -> tuple[dict[str, object], list[dict[str, Any]], list[dict[str, Any]]]:
    nav_rows = _rows(
        await session.execute(
            text(
                """SELECT date, class_code, class_name, nav, units_outstanding, fund_value
                     FROM lookthrough.nav_history
                    WHERE snapshot_id = :snapshot
                    ORDER BY date, class_code"""
            ),
            {"snapshot": snapshot_id},
        )
    )
    class_codes = {str(row["class_code"]) for row in nav_rows}
    nav_code = (
        share_class_code
        if share_class_code in class_codes
        else "0"
        if "0" in class_codes
        else str(nav_rows[0]["class_code"])
        if nav_rows
        else None
    )
    nav_points = [row for row in nav_rows if row["class_code"] == nav_code]
    nav_summary: dict[str, object] = {
        "class_code": nav_code,
        "class_name": nav_points[0]["class_name"] if nav_points else None,
        "points": nav_points,
        "change": (
            round(float(nav_points[-1]["nav"]) / float(nav_points[0]["nav"]) - 1, 8)
            if len(nav_points) > 1 and float(nav_points[0]["nav"])
            else None
        ),
    }

    cash = _rows(
        await session.execute(
            text(
                """SELECT currency, name, base_value, weight
                     FROM lookthrough.cash_balances
                    WHERE snapshot_id = :snapshot
                    ORDER BY abs(weight) DESC, ordinal"""
            ),
            {"snapshot": snapshot_id},
        )
    )
    contracts = _rows(
        await session.execute(
            text(
                """SELECT share_class_code, name, currency, delivery_date, bought_sold,
                          amount, current_value, unrealised_gain_loss, nav_impact, long_short
                     FROM lookthrough.currency_contracts
                    WHERE snapshot_id = :snapshot
                      AND (
                        NOT :holder_view
                        OR share_class_code IS NULL
                        OR share_class_code IN ('0', :class_code)
                      )
                    ORDER BY abs(COALESCE(nav_impact, 0)) DESC, ordinal"""
            ),
            {
                "snapshot": snapshot_id,
                "holder_view": holder_view,
                "class_code": share_class_code,
            },
        )
    )
    return nav_summary, cash, contracts


def _maturity_profile(positions: list[dict[str, Any]], as_of: date) -> list[dict[str, object]]:
    bands = [
        ("Within 1 year", 0, 1),
        ("1–3 years", 1, 3),
        ("3–5 years", 3, 5),
        ("5–10 years", 5, 10),
        ("Over 10 years", 10, None),
    ]
    grouped = {label: 0.0 for label, _, _ in bands}
    perpetual = 0.0
    for row in positions:
        maturity = row.get("maturity_date")
        weight = float(row["weight"])
        if maturity is None:
            if "PERP" in str(row.get("long_name") or row.get("name") or "").upper():
                perpetual += weight
            continue
        years = max(0.0, (maturity - as_of).days / 365.25)
        for label, lower, upper in bands:
            if years >= lower and (upper is None or years < upper):
                grouped[label] += weight
                break
    result = [{"label": label, "value": round(grouped[label], 8)} for label, _, _ in bands]
    if perpetual:
        result.append({"label": "Perpetual", "value": round(perpetual, 8)})
    return [row for row in result if row["value"]]


def _choose_snapshot(snapshots: list[dict[str, Any]], as_of: date | None) -> dict[str, Any] | None:
    return next(
        (row for row in snapshots if as_of is not None and row["as_of_date"] == as_of),
        snapshots[0] if as_of is None and snapshots else None,
    )


async def _composition(
    session: AsyncSession,
    *,
    classes: list[dict[str, Any]],
    as_of: date | None,
    share_class: dict[str, Any],
    holding: dict[str, object] | None,
    held_value: float | None,
) -> dict[str, object]:
    fund = classes[0]
    snapshots = await _fund_snapshots(session, int(fund["fund_id"]))
    chosen = _choose_snapshot(snapshots, as_of)
    if chosen is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund not found")
    positions = await _snapshot_positions(session, int(chosen["id"]))
    for row in positions:
        row["indirect_value"] = (
            round(held_value * float(row["weight"]), 2) if held_value is not None else 0
        )
        cost = row.get("book_cost")
        row["gain_loss"] = (
            round(float(row["market_value"]) - float(cost), 2) if cost is not None else None
        )
        row["gain_loss_percent"] = (
            round(float(row["market_value"]) / float(cost) - 1, 8)
            if cost is not None and float(cost)
            else None
        )
    nav_history, cash_balances, currency_contracts = await _snapshot_analytics(
        session,
        snapshot_id=int(chosen["id"]),
        share_class_code=str(share_class["class_code"]),
        holder_view=holding is not None,
    )
    coverage = float(chosen["coverage"])
    net_cash_weight = round(sum(float(row["weight"]) for row in cash_balances), 8)
    return {
        "fund": {
            "uid": fund["fund_uid"],
            "name": fund["fund_name"],
            "source": fund["source"],
            "fund_code": fund["fund_code"],
        },
        "share_class": {
            "isin": share_class["isin"],
            "sedol": share_class["sedol"],
            "name": share_class["name"],
        },
        "share_classes": [
            {
                "class_code": row["class_code"],
                "name": row["name"],
                "isin": row["isin"],
                "sedol": row["sedol"],
            }
            for row in classes
        ],
        "snapshot": {
            "uid": chosen["uid"],
            "as_of_date": chosen["as_of_date"],
            "coverage": coverage,
            "residual": max(0.0, round(1.0 - coverage, 8)),
            "position_count": chosen["position_count"],
        },
        "available_dates": [row["as_of_date"] for row in snapshots],
        "holding": holding,
        "positions": positions,
        "nav_history": nav_history,
        "cash": {
            "balances": cash_balances,
            "net_weight": net_cash_weight,
            "other_residual": round(1.0 - coverage - net_cash_weight, 8),
        },
        "maturity_profile": _maturity_profile(positions, chosen["as_of_date"]),
        "currency_contracts": currency_contracts,
        "breakdowns": {
            "country": _breakdown(positions, "country_name", "Unclassified"),
            "currency": _breakdown(positions, "currency", "Unclassified"),
            "asset_class": _breakdown(positions, "asset_class", "Other"),
        },
    }


@router.get("/admin/look-through/{fund_uid}", name="admin-get-look-through")
async def admin_get_lookthrough(
    fund_uid: str,
    as_of: date | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, object]:
    """Return the reference composition without requiring an operator to hold the fund."""
    require_permission(principal, LOOKTHROUGH_READ)
    classes = await _fund_classes(session, fund_uid)
    if not classes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund not found")
    return await _composition(
        session,
        classes=classes,
        as_of=as_of,
        share_class=classes[0],
        holding=None,
        held_value=None,
    )


@router.get("/crm/look-through/{fund_uid}", name="get-look-through")
async def get_lookthrough(
    fund_uid: str,
    as_of: date | None = None,
    account: str | None = None,
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """Return a composition only after proving a matching holding in the RLS-visible book."""
    account_id: int | None = None
    if account:
        account_id = (
            await session.execute(
                text("SELECT id FROM platform.accounts WHERE uid = :uid"), {"uid": account}
            )
        ).scalar_one_or_none()
        if account_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund not found")

    visible = await crm._visible_accounts(session, account_id=account_id, valued_only=True)
    holdings = crm._tag(await crm._firm_holdings(visible), visible)
    classes = await _fund_classes(session, fund_uid)
    class_by_isin = {row["isin"]: row for row in classes}
    matched: list[tuple[dict[str, object], Any]] = []
    for holding in holdings:
        isin = lookthrough.normalise_isin(holding.get("isin"))
        share_class = class_by_isin.get(isin)
        if share_class is not None:
            matched.append((holding, share_class))
    if not matched:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund not found")

    fund = matched[0][1]
    held_value = sum(crm._num(row.get("value")) for row, _ in matched)
    currency = next((str(row.get("currency")) for row, _ in matched if row.get("currency")), "GBP")
    return await _composition(
        session,
        classes=classes,
        as_of=as_of,
        share_class=dict(fund),
        holding={
            "value": round(held_value, 2),
            "currency": currency,
            "account_uid": account or matched[0][0].get("account_uid"),
            "account_name": matched[0][0].get("account_name"),
        },
        held_value=held_value,
    )
