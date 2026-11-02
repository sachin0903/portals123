"""File a GIA or ISA illustration from Graphene reference data.

The monthly engine is :mod:`app.projections`. This module gathers the rows it needs, refuses
with a named error when one is missing, and stores the request, the snapshot and the outputs
on ``platform.illustrations``. The PDF is a queued render of the code-owned template.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.projections import (
    Cashflow,
    IllustrationBlocked,
    ProjectionRequest,
    Tier,
    growth_from_target,
    present,
    project,
)
from app.render.illustration_brand import brand_for_illustration
from app.render.illustration_template import TEMPLATE_VERSION
from app.render.illustration_template_store import ensure_illustration_template

_ZERO = Decimal(0)
_ONE = Decimal(1)


class MoneyFlowIn(BaseModel):
    type: Literal["single", "regular"]
    amount: Decimal
    on: date | None = None
    start: date | None = None
    end: date | None = None
    frequency: Literal["monthly", "quarterly", "annual"] | None = None
    escalation: Decimal = _ZERO


class InvestmentIllustrationInput(BaseModel):
    """Adviser request for one GIA or ISA illustration. Identity comes from the client."""

    wrapper: Literal["GIA", "ISA"] | None = None
    start_date: date | None = None
    term_years: int = 10
    model_id: int | None = None
    contributions: list[MoneyFlowIn] = Field(default_factory=list)
    withdrawals: list[MoneyFlowIn] = Field(default_factory=list)
    existing_balance: Decimal = _ZERO
    other_platform_assets: Decimal = _ZERO
    adviser_initial_amount: Decimal | None = None
    adviser_initial_rate: Decimal | None = None
    adviser_ongoing: Decimal = _ZERO
    dfm_rate_override: Decimal | None = None
    fee_package_id: int | None = None
    joint_owner_name: str | None = None
    joint_owner_dob: date | None = None


class InvestmentIllustrationOut(BaseModel):
    illustration_uid: str | None = None
    document_uid: str | None = None
    stored: bool
    placeholder: bool = False
    placeholder_reason: str | None = None
    resolved_inputs: dict[str, object] = Field(default_factory=dict)
    status: str | None = None


def _dump(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money_gb(amount: Decimal) -> str:
    return f"£{amount:,.2f}"


def _pct(rate: Decimal) -> str:
    return f"{(rate * Decimal(100)):.2f}%"


def _flow(row: MoneyFlowIn) -> Cashflow:
    if row.amount <= 0:
        raise IllustrationBlocked("Each contribution and withdrawal must be greater than zero.")
    if row.escalation < 0 or row.escalation > Decimal("0.10"):
        raise IllustrationBlocked("Escalation must be between 0 and 10 percent.")
    if row.type == "single":
        if row.on is None:
            raise IllustrationBlocked("A single payment needs a date.")
        return Cashflow("single", row.amount, on=row.on)
    if row.start is None or row.frequency is None:
        raise IllustrationBlocked("A regular payment needs a start date and a frequency.")
    return Cashflow(
        "regular",
        row.amount,
        start=row.start,
        end=row.end,
        frequency=row.frequency,
        escalation=row.escalation,
    )


def _parse_tiers(raw: Any) -> tuple[Tier, ...]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not raw:
        raise IllustrationBlocked("The firm's fee schedule has no tiers.")
    tiers: list[Tier] = []
    for band in raw:
        if not isinstance(band, dict) or "from" not in band or "rate" not in band:
            raise IllustrationBlocked(
                "The firm's fee schedule has a tier without a from or a rate."
            )
        end = None if band.get("to") in (None, "") else _dec(band["to"])
        tiers.append(Tier(_dec(band["from"]), end, _dec(band["rate"])))
    return tuple(tiers)


def _schedule_from_package(row: dict[str, Any]) -> dict[str, Any]:
    """Turn a tenant fee package into the tiers the monthly engine charges.

    Package rates are percentage points (0.75 means 0.75%). The engine wants a fraction.
    A fixed ongoing amount is charged as that many pounds each month.
    """
    rule = row["rule"]
    if isinstance(rule, str):
        rule = json.loads(rule)
    if not isinstance(rule, dict):
        raise IllustrationBlocked(f"The fee package {row['name']} has no rule.")
    kind = str(row["fee_type"])
    monthly: Decimal | None = None
    if kind == "tiered":
        bands = rule.get("tiers")
        if not isinstance(bands, list) or not bands:
            raise IllustrationBlocked(f"The fee package {row['name']} has no tiers.")
        tiers = [
            {
                "from": band.get("from", 0),
                "to": band.get("to"),
                "rate": _dec(band["rate"]) / Decimal(100),
            }
            for band in bands
        ]
    elif kind == "percentage":
        tiers = [{"from": 0, "to": None, "rate": _dec(rule["rate"]) / Decimal(100)}]
    elif kind == "fixed":
        tiers = [{"from": 0, "to": None, "rate": _ZERO}]
        monthly = _dec(rule["amount"]) / Decimal(12)
    else:
        raise IllustrationBlocked(
            f"The fee package {row['name']} is not a charge this illustration can apply."
        )
    return {
        "uid": row["uid"],
        "name": row["name"],
        "fee_type": kind,
        "tiers": tiers,
        "tier_basis": "all_client_assets",
        "product_rate": _ZERO,
        "dealing_rate_exchange_traded": _ZERO,
        "dealing_rate_fund": _ZERO,
        "dealing_minimum": None,
        "minimum_monthly_fee": monthly,
    }


def _flow_rows(flows: list[MoneyFlowIn]) -> list[dict[str, str]]:
    rows = []
    for flow in flows:
        rows.append(
            {
                "from": (flow.on or flow.start or date.today()).strftime("%d %B %Y"),
                "to": flow.end.strftime("%d %B %Y") if flow.end else "—",
                "kind": "Single" if flow.type == "single" else "Regular",
                "amount": _money_gb(flow.amount),
                "frequency": "—" if flow.type == "single" else (flow.frequency or "—"),
                "escalation": "—" if flow.type == "single" else _pct(flow.escalation),
            }
        )
    return rows


async def _one(session: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = (await session.execute(text(sql), params)).mappings().one_or_none()
    return dict(row) if row else None


async def _all(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql), params)).mappings().all()]


async def create_wrapper_illustration(
    session: AsyncSession,
    contact_id: int,
    body: InvestmentIllustrationInput,
) -> InvestmentIllustrationOut | None:
    """Gather, project, and file. Raises :class:`IllustrationBlocked` before any insert."""
    client = await _one(
        session,
        "SELECT c.tenant_id, c.name, c.first_name, c.surname, c.dob "
        "FROM crm.contacts c WHERE c.id = :cid AND c.deleted_at IS NULL",
        {"cid": contact_id},
    )
    if client is None:
        return None
    if body.wrapper is None:
        raise IllustrationBlocked("Choose a GIA or an ISA.")
    if body.start_date is None:
        raise IllustrationBlocked("A plan start date is required.")
    if body.model_id is None:
        raise IllustrationBlocked("Choose a model.")
    if body.term_years < 1 or body.term_years > 40:
        raise IllustrationBlocked("The illustration period must be between 1 and 40 years.")
    if body.start_date < date.today():
        raise IllustrationBlocked("The plan start date must be today or later.")
    if body.adviser_ongoing < 0 or body.adviser_ongoing > Decimal("0.03"):
        raise IllustrationBlocked("The ongoing adviser charge must be between 0 and 3 percent.")
    if body.adviser_initial_amount is not None and body.adviser_initial_rate is not None:
        raise IllustrationBlocked(
            "Give the initial adviser charge as an amount or a rate, not both."
        )
    if not body.contributions and body.existing_balance <= 0:
        raise IllustrationBlocked("Enter a contribution or an existing balance.")
    if body.wrapper == "GIA" and body.joint_owner_name and body.joint_owner_dob is None:
        raise IllustrationBlocked("A joint owner needs a date of birth.")

    tenant_id = int(client["tenant_id"])
    firm = await _one(
        session,
        "SELECT name, legal_name, frn, chn, registered_office, illustration_warning, "
        "       gia_isa_illustrations "
        "FROM auth.tenants WHERE id = :id",
        {"id": tenant_id},
    )
    if firm is None or not firm["gia_isa_illustrations"]:
        raise IllustrationBlocked("This firm does not issue GIA or ISA illustrations.")
    missing_legal = [
        label
        for label, value in (
            ("legal entity", firm["legal_name"]),
            ("FCA number", firm["frn"]),
            ("company number", firm["chn"]),
            ("registered office", firm["registered_office"]),
            ("future-performance warning", firm["illustration_warning"]),
        )
        if value in (None, "")
    ]
    if missing_legal:
        raise IllustrationBlocked(
            "The firm's illustration disclosure is incomplete: " + ", ".join(missing_legal) + "."
        )

    model = await _one(
        session,
        "SELECT id, name, model_charge, target_return, provider::text AS provider "
        "FROM platform.models WHERE id = :id AND tenant_id = :tid",
        {"id": body.model_id, "tid": tenant_id},
    )
    if model is None:
        raise IllustrationBlocked("That model is not on this firm's book.")
    if model["target_return"] is None:
        raise IllustrationBlocked(
            "This model has no target return, so growth cannot be set. "
            "Choose a model with a target return."
        )
    warnings: list[str] = []
    low, mid, high, cap_warnings = growth_from_target(_dec(model["target_return"]), body.wrapper)
    warnings.extend(cap_warnings)

    packages = await _all(
        session,
        "SELECT id, uid, name, fee_type::text AS fee_type, rule "
        "FROM platform.fee_packages "
        "WHERE tenant_id = :tid AND coalesce(enabled, true) "
        "  AND category ILIKE '%platform%' "
        "  AND (effective_from IS NULL OR effective_from <= :on) "
        "  AND (effective_to IS NULL OR effective_to >= :on) "
        "  AND (applies_to IS NULL OR upper(:wrapper) = ANY ("
        "        SELECT upper(item) FROM unnest(applies_to) AS item)) "
        "ORDER BY sort_order, name",
        {"tid": tenant_id, "on": body.start_date, "wrapper": body.wrapper},
    )
    if not packages:
        raise IllustrationBlocked(
            "This firm has no platform fee package. Add one to the fee catalogue."
        )
    chosen = packages[0] if len(packages) == 1 and body.fee_package_id is None else None
    if body.fee_package_id is not None:
        chosen = next((row for row in packages if int(row["id"]) == body.fee_package_id), None)
        if chosen is None:
            raise IllustrationBlocked("That platform fee is not on this firm's catalogue.")
    if chosen is None:
        names = ", ".join(str(row["name"]) for row in packages)
        raise IllustrationBlocked(
            "This firm has more than one platform fee. Choose one: " + names + "."
        )
    schedule = _schedule_from_package(chosen)

    dfm = body.dfm_rate_override
    if dfm is None and model["model_charge"] is not None:
        dfm = _dec(model["model_charge"]) / Decimal(100)
    if dfm is None:
        dfm = _ZERO

    contributions = tuple(_flow(row) for row in body.contributions)
    withdrawals = tuple(_flow(row) for row in body.withdrawals)
    request = ProjectionRequest(
        wrapper=body.wrapper,
        start=body.start_date,
        term_years=body.term_years,
        contributions=contributions,
        withdrawals=withdrawals,
        existing_balance=body.existing_balance,
        other_platform_assets=body.other_platform_assets,
        adviser_initial_amount=body.adviser_initial_amount,
        adviser_initial_rate=body.adviser_initial_rate,
        adviser_ongoing=body.adviser_ongoing,
        dfm_rate=dfm,
        product_rate=_dec(schedule["product_rate"]),
        tiers=_parse_tiers(schedule["tiers"]),
        dealing_rate_exchange_traded=_dec(schedule["dealing_rate_exchange_traded"]),
        dealing_rate_fund=_dec(schedule["dealing_rate_fund"]),
        dealing_minimum=None
        if schedule["dealing_minimum"] is None
        else _dec(schedule["dealing_minimum"]),
        minimum_monthly_fee=None
        if schedule["minimum_monthly_fee"] is None
        else _dec(schedule["minimum_monthly_fee"]),
        growth_low=low,
        growth_mid=mid,
        growth_high=high,
        inflation=Decimal("0.02"),
        holdings=(),
    )
    result = project(request)
    result.warnings.extend(warnings)
    outputs = present(result, request)
    client_name = (
        " ".join(part for part in (client["first_name"], client["surname"]) if part)
        or client["name"]
        or "Client"
    )
    basis = {
        "this_wrapper": "this wrapper",
        "all_client_assets": "all of this client's assets on the platform",
        "family_linked": "family-linked holdings",
    }[str(schedule["tier_basis"])]
    if body.adviser_initial_amount is not None:
        initial_label = _money_gb(body.adviser_initial_amount)
    elif body.adviser_initial_rate is not None:
        initial_label = _pct(body.adviser_initial_rate) + " of the first contribution"
    else:
        initial_label = "none"
    raw_display = outputs["display"]
    if not isinstance(raw_display, dict):
        raise IllustrationBlocked("The projection did not produce a display.")
    display: dict[str, Any] = dict(raw_display)
    display.update(
        {
            "title": f"{body.wrapper} Illustration",
            "wrapper": body.wrapper,
            "reference": "",  # filled once the uid exists
            "prepared_at": datetime.now(ZoneInfo("Europe/London")).strftime("%d %B %Y, %H:%M"),
            "client_name": client_name,
            "dob": client["dob"].strftime("%d %B %Y") if client["dob"] else "—",
            "joint_owner": (
                f"{body.joint_owner_name}, born {body.joint_owner_dob.strftime('%d %B %Y')}"
                if body.wrapper == "GIA" and body.joint_owner_name and body.joint_owner_dob
                else None
            ),
            "start_date": body.start_date.strftime("%d %B %Y"),
            "term_years": body.term_years,
            "adviser_firm": firm["legal_name"],
            "model_name": model["name"],
            "contributions": _flow_rows(body.contributions)
            + (
                [
                    {
                        "from": body.start_date.strftime("%d %B %Y"),
                        "to": "—",
                        "kind": "Existing balance",
                        "amount": _money_gb(body.existing_balance),
                        "frequency": "—",
                        "escalation": "—",
                    }
                ]
                if body.existing_balance > 0
                else []
            ),
            "withdrawals": _flow_rows(body.withdrawals),
            "tiers": [
                {
                    "from": _money_gb(_dec(band["from"])),
                    "to": (
                        "and above" if band.get("to") in (None, "") else _money_gb(_dec(band["to"]))
                    ),
                    "rate": _pct(_dec(band["rate"])),
                }
                for band in schedule["tiers"]
            ],
            "tier_basis": basis,
            "other_assets": _money_gb(body.other_platform_assets)
            if body.other_platform_assets
            else None,
            "dfm_rate": _pct(dfm),
            "product_rate": _pct(_dec(schedule["product_rate"])),
            "cash_paid": "—",
            "cash_retained": "—",
            "adviser_initial": initial_label,
            "adviser_ongoing": _pct(body.adviser_ongoing),
            "assets": [],
            "cost_date": "—",
            "charge_rows": [
                ("Platform", display["charges"]["platform"]),
                ("Discretionary manager", display["charges"]["discretionary_manager"]),
                ("Product", display["charges"]["product"]),
                ("Adviser", display["charges"]["adviser"]),
            ],
            "legal_name": firm["legal_name"],
            "fca": str(firm["frn"]),
            "company_number": firm["chn"],
            "registered_office": firm["registered_office"],
            "warning": firm["illustration_warning"],
            "custodian_name": "—",
            "custodian_fca": "—",
            "warnings": (
                list(outputs["warnings"]) if isinstance(outputs["warnings"], list) else []
            ),
            "target_return": f"{_dec(model['target_return']):.2f}%",
            "growth_spread": f"{((high - mid) * Decimal(100)):.0f} percentage points",
            **await brand_for_illustration(session, tenant_id),
        }
    )
    outputs["display"] = display

    snapshot = {
        "template_version": TEMPLATE_VERSION,
        "target_return": str(model["target_return"]),
        "growth": {
            "low": str(low),
            "mid": str(mid),
            "high": str(high),
            "inflation": "0.02",
            "spread": "0.03",
        },
        "fee_package": {
            "uid": schedule["uid"],
            "name": schedule["name"],
            "fee_type": schedule["fee_type"],
            "tiers": schedule["tiers"],
        },
        "legal": {
            "legal_name": firm["legal_name"],
            "frn": firm["frn"],
            "chn": firm["chn"],
            "registered_office": firm["registered_office"],
            "warning": firm["illustration_warning"],
        },
        "brand": {
            "color": display["brand_color"],
            "ink": display["ink_color"],
            "has_logo": bool(display["logo"]),
        },
    }
    request_dump = body.model_dump(mode="json")
    uid = new_id()
    doc_uid = new_id()
    display["reference"] = uid
    recorded = {
        "product_type": body.wrapper,
        "quotation_type": "Costs and charges",
        "term_years": body.term_years,
        "existing_fund_value": float(body.existing_balance),
        "output_reference": uid,
        "placeholder": False,
        "placeholder_reason": None,
        "model": {"id": body.model_id, "name": model["name"]},
        "request": request_dump,
    }
    document_inputs = {
        "request": request_dump,
        "reference": snapshot,
        "outputs": outputs,
        "display": display,
        "template_version": TEMPLATE_VERSION,
    }
    name = f"{body.wrapper} illustration"
    template_version_id = await ensure_illustration_template(session)
    document_id = (
        await session.execute(
            text(
                "INSERT INTO documents.documents ("
                "  uid, tenant_id, document_type, source, name, status, inputs, metadata, "
                "  generated_by, intended_contact_id, template_version_id"
                ") VALUES ("
                "  :uid, :tid, 'illustration', 'generated', :name, 'queued', "
                "  CAST(:inputs AS jsonb), CAST(:meta AS jsonb), auth.uid(), :cid, :vid"
                ") RETURNING id"
            ),
            {
                "uid": doc_uid,
                "tid": tenant_id,
                "name": name,
                "inputs": _dump(document_inputs),
                "meta": _dump(
                    {"illustration_engine": "graphene", "template_version": TEMPLATE_VERSION}
                ),
                "cid": contact_id,
                "vid": template_version_id,
            },
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO documents.render_jobs (tenant_id, document_id) VALUES (:tid, :did)"),
        {"tid": tenant_id, "did": document_id},
    )
    await session.execute(
        text(
            "INSERT INTO platform.illustrations ("
            "  uid, tenant_id, contact_id, document_id, name, product_type, quotation_type, "
            "  existing_fund_value, output_reference, placeholder, inputs, reference_snapshot, "
            "  outputs, created_by"
            ") VALUES ("
            "  :uid, :tid, :cid, :did, :name, :product, 'Costs and charges', :fund, :ref, false, "
            "  CAST(:inputs AS jsonb), CAST(:snapshot AS jsonb), "
            "  CAST(:outputs AS jsonb), auth.uid()"
            ")"
        ),
        {
            "uid": uid,
            "tid": tenant_id,
            "cid": contact_id,
            "did": document_id,
            "name": name,
            "product": body.wrapper,
            "fund": body.existing_balance,
            "ref": uid,
            "inputs": _dump(recorded),
            "snapshot": _dump(snapshot),
            "outputs": _dump(outputs),
        },
    )
    return InvestmentIllustrationOut(
        illustration_uid=uid,
        document_uid=doc_uid,
        stored=True,
        status="generating",
        resolved_inputs=recorded,
    )
