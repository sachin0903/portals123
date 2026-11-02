"""Reference data a GIA or ISA illustration copies onto itself.

Growth rates and the ISA allowance are Graphene-wide. Cash interest and the custodian footer
are per custodian. The fee schedule, the legal disclosure and the model mix are per firm.
Instrument costs are global, one row per ISIN and data date. Nothing here is read from a
custodian at generation time.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.addressing import admin_tenant_id
from app.auth import Principal, get_current_principal, require_any_permission, require_permission
from app.db import admin_session
from app.permissions import PLATFORMS_READ, PLATFORMS_WRITE, TENANTS_READ, TENANTS_WRITE

router = APIRouter(prefix="/admin", tags=["admin"])

Provider = Literal["sei", "seccl", "morgan-lloyd"]
Wrapper = Literal["GIA", "ISA"]
TierBasis = Literal["this_wrapper", "all_client_assets", "family_linked"]
InstrumentType = Literal["fund", "etf", "equity", "cash"]


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql), params)).mappings().all()]


class GrowthSetIn(BaseModel):
    effective_from: date
    equity_rate: Decimal
    gilts_rate: Decimal
    bonds_rate: Decimal
    other_rate: Decimal
    spread: Decimal = Decimal("0.03")
    gia_haircut: Decimal = Decimal("0.005")
    inflation: Decimal = Decimal("0.02")
    approved: bool = False


class IsaAllowanceIn(BaseModel):
    tax_year: str
    allowance: Decimal
    effective_from: date


class CustodyIn(BaseModel):
    provider: Provider
    effective_from: date
    cash_paid_rate: Decimal = Decimal(0)
    cash_retention_rate: Decimal = Decimal(0)
    custodian_name: str
    custodian_fca: str


class InstrumentCostIn(BaseModel):
    isin: str
    data_date: date
    instrument_type: InstrumentType
    oneoff_entry: Decimal = Decimal(0)
    ongoing: Decimal = Decimal(0)
    transaction_cost: Decimal = Decimal(0)
    incidental: Decimal = Decimal(0)
    name: str | None = None


class DisclosureIn(BaseModel):
    legal_name: str | None = None
    frn: int | None = None
    chn: str | None = None
    registered_office: str | None = None
    illustration_warning: str | None = None
    gia_isa_illustrations: bool = False
    # Every illustration (SIPP included), not only GIA/ISA: show it in the client portal the moment
    # it renders. Off, staff share each one from the document (0248, FB-20261006-04).
    illustrations_auto_share: bool = False


class TierIn(BaseModel):
    from_amount: Decimal = Field(alias="from")
    to: Decimal | None = None
    rate: Decimal

    model_config = {"populate_by_name": True}


class FeeScheduleIn(BaseModel):
    wrapper: Wrapper
    tiers: list[TierIn]
    tier_basis: TierBasis = "all_client_assets"
    product_rate: Decimal = Decimal(0)
    dealing_rate_exchange_traded: Decimal = Decimal(0)
    dealing_rate_fund: Decimal = Decimal(0)
    dealing_minimum: Decimal | None = None
    minimum_monthly_fee: Decimal | None = None
    effective_from: date


class HoldingIn(BaseModel):
    isin: str | None = None
    name: str | None = None
    percentage: Decimal


class ModelFactsIn(BaseModel):
    model_charge: Decimal | None = None
    asset_mix: dict[str, Decimal]
    holdings: list[HoldingIn]


def _platforms_read(principal: Principal = Depends(get_current_principal)) -> Principal:
    require_permission(principal, PLATFORMS_READ)
    return principal


def _platforms_write(principal: Principal = Depends(get_current_principal)) -> Principal:
    require_permission(principal, PLATFORMS_WRITE)
    return principal


def _firm_read(principal: Principal = Depends(get_current_principal)) -> Principal:
    require_any_permission(principal, PLATFORMS_READ, TENANTS_READ)
    return principal


def _firm_write(principal: Principal = Depends(get_current_principal)) -> Principal:
    require_any_permission(principal, PLATFORMS_WRITE, TENANTS_WRITE)
    return principal


@router.get("/illustration-reference/growth-sets")
async def list_growth_sets(
    _: Principal = Depends(_platforms_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT uid, effective_from, equity_rate, gilts_rate, bonds_rate, other_rate, "
        "       spread, gia_haircut, inflation, approved_at "
        "FROM platform.illustration_growth_sets ORDER BY effective_from DESC",
        {},
    )


@router.post("/illustration-reference/growth-sets", status_code=status.HTTP_201_CREATED)
async def create_growth_set(
    body: GrowthSetIn,
    principal: Principal = Depends(_platforms_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, Any]:
    uid = (
        await session.execute(
            text(
                "INSERT INTO platform.illustration_growth_sets ("
                "  effective_from, equity_rate, gilts_rate, bonds_rate, other_rate, spread, "
                "  gia_haircut, inflation, approved_at, approved_by"
                ") VALUES ("
                "  :on, :equity, :gilts, :bonds, :other, :spread, :haircut, :inflation, "
                "  CASE WHEN :approved THEN now() ELSE NULL END, "
                "  CASE WHEN :approved THEN CAST(:by AS uuid) ELSE NULL END"
                ") RETURNING uid"
            ),
            {
                "on": body.effective_from,
                "equity": body.equity_rate,
                "gilts": body.gilts_rate,
                "bonds": body.bonds_rate,
                "other": body.other_rate,
                "spread": body.spread,
                "haircut": body.gia_haircut,
                "inflation": body.inflation,
                "approved": body.approved,
                "by": principal.user_id if _uuid(principal.user_id) else None,
            },
        )
    ).scalar_one()
    return {"uid": uid}


@router.get("/illustration-reference/isa-allowances")
async def list_isa_allowances(
    _: Principal = Depends(_platforms_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT tax_year, allowance, effective_from FROM platform.illustration_isa_allowances "
        "ORDER BY effective_from DESC",
        {},
    )


@router.post("/illustration-reference/isa-allowances", status_code=status.HTTP_201_CREATED)
async def create_isa_allowance(
    body: IsaAllowanceIn,
    _: Principal = Depends(_platforms_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, str]:
    await session.execute(
        text(
            "INSERT INTO platform.illustration_isa_allowances "
            "(tax_year, allowance, effective_from) "
            "VALUES (:y, :a, :on) "
            "ON CONFLICT (tax_year) DO UPDATE SET allowance = EXCLUDED.allowance, "
            "effective_from = EXCLUDED.effective_from"
        ),
        {"y": body.tax_year, "a": body.allowance, "on": body.effective_from},
    )
    return {"tax_year": body.tax_year}


@router.get("/illustration-reference/custody")
async def list_custody(
    _: Principal = Depends(_platforms_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT provider::text AS provider, effective_from, cash_paid_rate, cash_retention_rate, "
        "       custodian_name, custodian_fca "
        "FROM platform.illustration_custody ORDER BY provider, effective_from DESC",
        {},
    )


@router.post("/illustration-reference/custody", status_code=status.HTTP_201_CREATED)
async def create_custody(
    body: CustodyIn,
    _: Principal = Depends(_platforms_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, str]:
    await session.execute(
        text(
            "INSERT INTO platform.illustration_custody ("
            "  provider, effective_from, cash_paid_rate, cash_retention_rate, "
            "  custodian_name, custodian_fca"
            ") VALUES (CAST(:p AS platform.provider_code), :on, :paid, :kept, :name, :fca) "
            "ON CONFLICT (provider, effective_from) DO UPDATE SET "
            "  cash_paid_rate = EXCLUDED.cash_paid_rate, "
            "  cash_retention_rate = EXCLUDED.cash_retention_rate, "
            "  custodian_name = EXCLUDED.custodian_name, "
            "  custodian_fca = EXCLUDED.custodian_fca"
        ),
        {
            "p": body.provider,
            "on": body.effective_from,
            "paid": body.cash_paid_rate,
            "kept": body.cash_retention_rate,
            "name": body.custodian_name,
            "fca": body.custodian_fca,
        },
    )
    return {"provider": body.provider}


@router.get("/illustration-reference/instrument-costs")
async def list_instrument_costs(
    isin: str | None = None,
    _: Principal = Depends(_platforms_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT isin, data_date, instrument_type, oneoff_entry, ongoing, transaction_cost, "
        "       incidental, name "
        "FROM platform.instrument_costs "
        # Cast the optional filter so Postgres can type the bare parameter: `:isin IS NULL` on its
        # own is AmbiguousParameter when isin is None (nothing else fixes its type).
        "WHERE (CAST(:isin AS text) IS NULL OR isin = :isin) "
        "ORDER BY data_date DESC LIMIT 200",
        {"isin": isin},
    )


@router.post("/illustration-reference/instrument-costs", status_code=status.HTTP_201_CREATED)
async def import_instrument_costs(
    body: list[InstrumentCostIn],
    _: Principal = Depends(_platforms_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, int]:
    for row in body:
        await session.execute(
            text(
                "INSERT INTO platform.instrument_costs ("
                "  isin, data_date, instrument_type, oneoff_entry, ongoing, transaction_cost, "
                "  incidental, name"
                ") VALUES (:isin, :on, :kind, :oneoff, :ongoing, :txn, :incidental, :name) "
                "ON CONFLICT (isin, data_date) DO UPDATE SET "
                "  instrument_type = EXCLUDED.instrument_type, "
                "  oneoff_entry = EXCLUDED.oneoff_entry, ongoing = EXCLUDED.ongoing, "
                "  transaction_cost = EXCLUDED.transaction_cost, incidental = EXCLUDED.incidental, "
                "  name = EXCLUDED.name"
            ),
            {
                "isin": row.isin,
                "on": row.data_date,
                "kind": row.instrument_type,
                "oneoff": row.oneoff_entry,
                "ongoing": row.ongoing,
                "txn": row.transaction_cost,
                "incidental": row.incidental,
                "name": row.name,
            },
        )
    return {"imported": len(body)}


@router.get("/tenants/{tenant_uid}/illustration-disclosure")
async def get_disclosure(
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_read),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT legal_name, frn, chn, registered_office, illustration_warning, "
                    "       gia_isa_illustrations, illustrations_auto_share "
                    "FROM auth.tenants WHERE id = :id"
                ),
                {"id": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


@router.put("/tenants/{tenant_uid}/illustration-disclosure")
async def put_disclosure(
    body: DisclosureIn,
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, bool]:
    await session.execute(
        text(
            "UPDATE auth.tenants SET legal_name = :legal, frn = :frn, chn = :chn, "
            "  registered_office = :office, illustration_warning = :warning, "
            "  gia_isa_illustrations = :flag, illustrations_auto_share = :auto_share "
            "WHERE id = :id"
        ),
        {
            "id": tenant_id,
            "legal": body.legal_name,
            "frn": body.frn,
            "chn": body.chn,
            "office": body.registered_office,
            "warning": body.illustration_warning,
            "flag": body.gia_isa_illustrations,
            "auto_share": body.illustrations_auto_share,
        },
    )
    return {"ok": True}


@router.get("/tenants/{tenant_uid}/illustration-fee-schedules")
async def list_fee_schedules(
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT uid, wrapper, tiers, tier_basis, product_rate, dealing_rate_exchange_traded, "
        "       dealing_rate_fund, dealing_minimum, minimum_monthly_fee, effective_from "
        "FROM platform.illustration_fee_schedules WHERE tenant_id = :id "
        "ORDER BY wrapper, effective_from DESC",
        {"id": tenant_id},
    )


@router.post(
    "/tenants/{tenant_uid}/illustration-fee-schedules", status_code=status.HTTP_201_CREATED
)
async def create_fee_schedule(
    body: FeeScheduleIn,
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, str]:
    tiers = [
        {
            "from": str(band.from_amount),
            "to": None if band.to is None else str(band.to),
            "rate": str(band.rate),
        }
        for band in body.tiers
    ]
    uid = (
        await session.execute(
            text(
                "INSERT INTO platform.illustration_fee_schedules ("
                "  tenant_id, wrapper, tiers, tier_basis, product_rate, "
                "  dealing_rate_exchange_traded, dealing_rate_fund, dealing_minimum, "
                "  minimum_monthly_fee, effective_from"
                ") VALUES ("
                "  :tid, :wrapper, CAST(:tiers AS jsonb), :basis, :product, :etf, :fund, "
                "  :minimum, :monthly, :on"
                ") ON CONFLICT (tenant_id, wrapper, effective_from) DO UPDATE SET "
                "  tiers = EXCLUDED.tiers, tier_basis = EXCLUDED.tier_basis, "
                "  product_rate = EXCLUDED.product_rate, "
                "  dealing_rate_exchange_traded = EXCLUDED.dealing_rate_exchange_traded, "
                "  dealing_rate_fund = EXCLUDED.dealing_rate_fund, "
                "  dealing_minimum = EXCLUDED.dealing_minimum, "
                "  minimum_monthly_fee = EXCLUDED.minimum_monthly_fee "
                "RETURNING uid"
            ),
            {
                "tid": tenant_id,
                "wrapper": body.wrapper,
                "tiers": _dump(tiers),
                "basis": body.tier_basis,
                "product": body.product_rate,
                "etf": body.dealing_rate_exchange_traded,
                "fund": body.dealing_rate_fund,
                "minimum": body.dealing_minimum,
                "monthly": body.minimum_monthly_fee,
                "on": body.effective_from,
            },
        )
    ).scalar_one()
    return {"uid": uid}


@router.get("/tenants/{tenant_uid}/illustration-models")
async def list_illustration_models(
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_read),
    session: AsyncSession = Depends(admin_session),
) -> list[dict[str, Any]]:
    return await _rows(
        session,
        "SELECT m.uid, m.name, m.model_charge, m.provider::text AS provider, "
        "       v.asset_mix, v.version "
        "FROM platform.models m "
        "LEFT JOIN LATERAL ("
        "  SELECT asset_mix, version FROM platform.model_versions "
        "  WHERE model_id = m.id AND published_at IS NOT NULL "
        "  ORDER BY version DESC LIMIT 1"
        ") v ON true "
        "WHERE m.tenant_id = :id ORDER BY m.name",
        {"id": tenant_id},
    )


@router.put("/tenants/{tenant_uid}/illustration-models/{model_uid}")
async def put_model_facts(
    model_uid: str,
    body: ModelFactsIn,
    tenant_id: int = Depends(admin_tenant_id),
    _: Principal = Depends(_firm_write),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, bool]:
    keys = ("equity", "gilts", "bonds", "other", "cash")
    if any(key not in body.asset_mix for key in keys):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset mix needs equity, gilts, bonds, other and cash.",
        )
    model_id = (
        await session.execute(
            text("SELECT id FROM platform.models WHERE uid = :uid AND tenant_id = :tid"),
            {"uid": model_uid, "tid": tenant_id},
        )
    ).scalar_one_or_none()
    if model_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    await session.execute(
        text("UPDATE platform.models SET model_charge = :charge WHERE id = :id"),
        {"charge": body.model_charge, "id": model_id},
    )
    version_id = (
        await session.execute(
            text(
                "SELECT id FROM platform.model_versions "
                "WHERE model_id = :id AND published_at IS NOT NULL "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"id": model_id},
        )
    ).scalar_one_or_none()
    mix = _dump({key: str(body.asset_mix[key]) for key in keys})
    if version_id is None:
        version_id = (
            await session.execute(
                text(
                    "INSERT INTO platform.model_versions ("
                    "  tenant_id, model_id, version, asset_mix, published_at, note"
                    ") VALUES ("
                    "  :tid, :mid, 1, CAST(:mix AS jsonb), now(), 'Entered for illustrations'"
                    ") RETURNING id"
                ),
                {"tid": tenant_id, "mid": model_id, "mix": mix},
            )
        ).scalar_one()
    else:
        await session.execute(
            text(
                "UPDATE platform.model_versions SET asset_mix = CAST(:mix AS jsonb) WHERE id = :id"
            ),
            {"mix": mix, "id": version_id},
        )
        await session.execute(
            text("DELETE FROM platform.model_allocations WHERE model_version_id = :id"),
            {"id": version_id},
        )
    for index, holding in enumerate(body.holdings):
        await session.execute(
            text(
                "INSERT INTO platform.model_allocations "
                "(tenant_id, model_version_id, isin, name, percentage, sort_order) "
                "VALUES (:tid, :vid, :isin, :name, :pct, :ord)"
            ),
            {
                "tid": tenant_id,
                "vid": version_id,
                "isin": holding.isin,
                "name": holding.name,
                "pct": holding.percentage,
                "ord": index,
            },
        )
    return {"ok": True}


def _uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        from uuid import UUID

        UUID(value)
    except ValueError:
        return False
    return True
