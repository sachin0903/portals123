import hashlib
import json
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import lookthrough
from app.auth import Principal
from app.lookthrough import (
    WorkbookError,
    import_workbook,
    normalise_isin,
    parse_workbook,
    valid_isin,
)
from app.routers import lookthrough as lookthrough_router


def workbook_bytes(
    *,
    control_date: datetime | None = None,
    market_value: float = 1050,
) -> bytes:
    book = Workbook()
    control = book.active
    control.title = "IOControl"
    valuation = book.create_sheet("Valuation")
    nav = book.create_sheet("DailyNAV")
    cash = book.create_sheet("Cash_at_Bank")
    hedges = book.create_sheet("Open_Currency_Contracts")
    report_date = datetime(2026, 8, 28)
    control["E2"] = 491
    control["E3"] = control_date or report_date
    classes = [
        ("48", "IFSL Arbion Balanced Opportunities X Acc", "GB00BN7HRC50", "BN7HRC5"),
        ("47", "IFSL Arbion Balanced Opportunities X Inc", "GB00BN7HRD67", "BN7HRD6"),
    ]
    for row, (code, name, isin, sedol) in enumerate(classes, 3):
        control.cell(row, 27, 491)
        control.cell(row, 28, code)
        control.cell(row, 29, name)
        control.cell(row, 30, isin)
        control.cell(row, 31, sedol)
    valuation["A7"] = report_date
    valuation.cell(12, 1, "0682538")
    valuation.cell(12, 2, "GB0006825383")
    valuation.cell(12, 4, "PERSIMMON PLC")
    valuation.cell(12, 7, "PERSIMMON PLC ORD")
    valuation.cell(12, 9, datetime(2030, 7, 22))
    valuation.cell(12, 10, "GBP")
    valuation.cell(12, 12, 100)
    valuation.cell(12, 13, 10.5)
    valuation.cell(12, 14, 900)
    valuation.cell(12, 18, "S")
    valuation.cell(12, 20, market_value)
    valuation.cell(12, 21, 10)
    valuation.cell(12, 22, 0.05)
    valuation.cell(12, 23, 0.75)
    valuation.cell(12, 24, "UNITED KINGDOM")
    valuation.cell(12, 25, "GB")
    valuation.cell(13, 4, "TOTAL VALUE OF SECURITIES")
    valuation.cell(13, 20, 1050)
    valuation.cell(13, 23, 1)
    nav.cell(9, 1, 491)
    nav.cell(9, 2, 48)
    nav.cell(9, 3, "IFSL Arbion Balanced Opportunities X Acc")
    nav.cell(9, 4, datetime(2026, 8, 27))
    nav.cell(9, 6, 1000)
    nav.cell(9, 7, 100_000)
    nav.cell(9, 15, 100)
    nav.cell(10, 1, 491)
    nav.cell(10, 2, 48)
    nav.cell(10, 3, "IFSL Arbion Balanced Opportunities X Acc")
    nav.cell(10, 4, report_date)
    nav.cell(10, 6, 1000)
    nav.cell(10, 7, 102_000)
    nav.cell(10, 15, 102)
    cash.cell(12, 1, "GBP")
    cash.cell(12, 3, "BRITISH POUND")
    cash.cell(12, 10, 25_000)
    cash.cell(12, 16, 0.02)
    hedges.cell(12, 2, "X Acc")
    hedges.cell(12, 3, 48)
    hedges.cell(12, 4, "USD HEDGE")
    hedges.cell(12, 5, "USD")
    hedges.cell(12, 9, datetime(2026, 9, 30))
    hedges.cell(12, 11, "Sold")
    hedges.cell(12, 14, 100_000)
    hedges.cell(12, 17, 98_000)
    hedges.cell(12, 20, 2_000)
    hedges.cell(12, 22, "Short")
    hedges.cell(12, 23, 0.002)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_parses_verified_ifsl_sections_and_excludes_total() -> None:
    parsed = parse_workbook(workbook_bytes())
    assert parsed.fund_code == "491"
    assert parsed.fund_name == "IFSL Arbion Balanced Opportunities"
    assert parsed.as_of_date.isoformat() == "2026-08-28"
    assert [item.isin for item in parsed.share_classes] == [
        "GB00BN7HRC50",
        "GB00BN7HRD67",
    ]
    assert len(parsed.positions) == 1
    assert parsed.positions[0].asset_class == "Equity"
    assert parsed.positions[0].country_code == "GB"
    assert parsed.positions[0].maturity_date == datetime(2030, 7, 22).date()
    assert parsed.positions[0].prior_price == 10
    assert parsed.positions[0].movement_percent == Decimal("0.05")
    assert [point.nav for point in parsed.nav_history] == [Decimal("100"), Decimal("102")]
    assert parsed.cash_balances[0].base_value == Decimal("25000")
    assert parsed.currency_contracts[0].unrealised_gain_loss == Decimal("2000")


def test_refuses_disagreeing_report_dates() -> None:
    with pytest.raises(WorkbookError, match="do not agree"):
        parse_workbook(workbook_bytes(control_date=datetime(2026, 8, 27)))


def test_refuses_missing_required_sheet() -> None:
    book = Workbook()
    output = BytesIO()
    book.save(output)
    with pytest.raises(WorkbookError, match="required sheet"):
        parse_workbook(output.getvalue())


def test_refuses_expanded_workbook_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lookthrough, "MAX_WORKBOOK_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(WorkbookError, match="expanded workbook"):
        parse_workbook(workbook_bytes())


def test_isin_normalisation_and_checksum() -> None:
    assert normalise_isin(" gb00 bn7hrc50 ") == "GB00BN7HRC50"
    assert valid_isin("GB00BN7HRC50")
    assert not valid_isin("GB00BN7HRC51")


@pytest.mark.asyncio
async def test_import_is_idempotent_and_preserves_same_date_correction(
    service_session: AsyncSession,
) -> None:
    first_bytes = workbook_bytes()
    first = await import_workbook(
        service_session,
        content=first_bytes,
        filename="first.xlsx",
        imported_by=None,
    )
    duplicate = await import_workbook(
        service_session,
        content=first_bytes,
        filename="duplicate.xlsx",
        imported_by=None,
    )
    snapshot_id = (
        await service_session.execute(
            text(
                """SELECT s.id
                     FROM lookthrough.snapshots s
                     JOIN lookthrough.funds f ON f.id = s.fund_id
                    WHERE f.fund_code = '491' AND s.file_sha256 = :digest"""
            ),
            {"digest": hashlib.sha256(first_bytes).hexdigest()},
        )
    ).scalar_one()
    await service_session.execute(
        text("DELETE FROM lookthrough.nav_history WHERE snapshot_id = :snapshot"),
        {"snapshot": snapshot_id},
    )
    await service_session.execute(
        text("DELETE FROM lookthrough.cash_balances WHERE snapshot_id = :snapshot"),
        {"snapshot": snapshot_id},
    )
    await service_session.execute(
        text("DELETE FROM lookthrough.currency_contracts WHERE snapshot_id = :snapshot"),
        {"snapshot": snapshot_id},
    )
    await service_session.execute(
        text(
            """UPDATE lookthrough.positions
                  SET prior_price = NULL, movement_percent = NULL
                WHERE snapshot_id = :snapshot"""
        ),
        {"snapshot": snapshot_id},
    )
    enriched = await import_workbook(
        service_session,
        content=first_bytes,
        filename="enrich-existing.xlsx",
        imported_by=None,
    )
    correction = await import_workbook(
        service_session,
        content=workbook_bytes(market_value=1100),
        filename="correction.xlsx",
        imported_by=None,
    )
    assert first["status"] == "imported"
    assert duplicate["status"] == "unchanged"
    assert enriched["status"] == "enriched"
    assert correction["status"] == "corrected"
    rows = (
        (
            await service_session.execute(
                text(
                    """SELECT s.uid, old.uid AS supersedes_uid
                     FROM lookthrough.snapshots s
                     LEFT JOIN lookthrough.snapshots old ON old.id = s.supersedes_id
                     JOIN lookthrough.funds f ON f.id = s.fund_id
                    WHERE f.fund_code = '491'
                    ORDER BY s.imported_at, s.id"""
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    assert rows[1]["supersedes_uid"] == rows[0]["uid"]


@pytest.mark.asyncio
async def test_operations_register_and_detail_expose_every_matching_isin(
    service_session: AsyncSession,
) -> None:
    imported = await import_workbook(
        service_session,
        content=workbook_bytes(),
        filename="balanced.xlsx",
        imported_by=None,
    )
    principal = Principal(
        user_id="operations-test",
        permissions=["lookthrough:read"],
    )

    register: Any = await lookthrough_router.admin_list(
        principal=principal, session=service_session
    )
    fund = next(row for row in register["funds"] if row["uid"] == imported["fund_uid"])
    assert fund["isins"] == ["GB00BN7HRC50", "GB00BN7HRD67"]

    detail: Any = await lookthrough_router.admin_get_lookthrough(
        str(imported["fund_uid"]),
        principal=principal,
        session=service_session,
    )
    assert detail["holding"] is None
    assert [row["isin"] for row in detail["share_classes"]] == [
        "GB00BN7HRC50",
        "GB00BN7HRD67",
    ]
    assert detail["positions"][0]["name"] == "PERSIMMON PLC"
    assert detail["positions"][0]["gain_loss"] == pytest.approx(
        detail["positions"][0]["market_value"] - detail["positions"][0]["book_cost"]
    )
    assert detail["nav_history"]["change"] == pytest.approx(0.02)
    assert [point["nav"] for point in detail["nav_history"]["points"]] == [100.0, 102.0]
    assert detail["cash"]["net_weight"] == pytest.approx(0.02)
    assert detail["maturity_profile"] == [{"label": "3–5 years", "value": 0.75}]
    assert detail["currency_contracts"][0]["name"] == "USD HEDGE"
    assert detail["currency_contracts"][0]["nav_impact"] == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_numerics_reach_the_portals_as_json_numbers(
    service_session: AsyncSession,
) -> None:
    """A Decimal serialises as a JSON string, which every reader types as a number.

    Both routes declare ``dict[str, object]``, so FastAPI hands the payload to pydantic rather
    than to ``jsonable_encoder``. A string weight fails ``Number.isFinite`` in the portals with
    no error anywhere: the register printed the raw ratio and the treemap reported that the
    snapshot had no weights at all.
    """
    imported = await import_workbook(
        service_session,
        content=workbook_bytes(),
        filename="balanced.xlsx",
        imported_by=None,
    )
    principal = Principal(user_id="operations-test", permissions=["lookthrough:read"])
    register: Any = await lookthrough_router.admin_list(
        principal=principal, session=service_session
    )
    detail: Any = await lookthrough_router.admin_get_lookthrough(
        str(imported["fund_uid"]), principal=principal, session=service_session
    )

    wire = json.loads(TypeAdapter(dict[str, object]).dump_json(register))
    fund = next(row for row in wire["funds"] if row["uid"] == imported["fund_uid"])
    assert isinstance(fund["coverage"], float)
    position = json.loads(TypeAdapter(dict[str, object]).dump_json(detail))["positions"][0]
    for key in (
        "weight",
        "quantity",
        "price",
        "prior_price",
        "movement_percent",
        "book_cost",
        "market_value",
        "gain_loss",
        "gain_loss_percent",
    ):
        assert isinstance(position[key], float), key
    assert isinstance(
        json.loads(TypeAdapter(dict[str, object]).dump_json(detail))["nav_history"]["points"][0][
            "nav"
        ],
        float,
    )
