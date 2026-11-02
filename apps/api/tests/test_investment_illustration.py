"""GIA and ISA illustrations: a missing fact files nothing; a complete request files a snapshot."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.render.illustration_brand import DEFAULT_ACCENT, DEFAULT_INK
from app.render.illustration_template import TEMPLATE_CSS, TEMPLATE_HTML
from app.render.jinja_engine import render_with_diagnostics
from app.routers import illustrations


def _principal() -> Principal:
    return Principal(
        user_id="wos", organisation_id="org", role="adviser", permissions=["illustrations:write"]
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


@asynccontextmanager
async def _svc(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_template_prints_stored_figures_without_recalculating() -> None:
    html, _empty = render_with_diagnostics(
        TEMPLATE_HTML,
        {
            "display": {
                "title": "GIA Illustration",
                "reference": "abc",
                "prepared_at": "28 September 2026",
                "warning": "Forecasts are not a reliable indicator of future performance.",
                "wrapper": "GIA",
                "client_name": "Ada Lovelace",
                "dob": "27 May 1988",
                "joint_owner": None,
                "start_date": "23 September 2026",
                "term_years": 15,
                "adviser_firm": "Select Advice Ltd",
                "contributions": [],
                "withdrawals": [],
                "projected_low": "£68,773",
                "projected_mid": "£107,827",
                "projected_high": "£166,840",
                "exhausted_low": None,
                "exhausted_mid": None,
                "exhausted_high": None,
                "growth_low": "-1.22%",
                "growth_mid": "1.78%",
                "growth_high": "4.78%",
                "inflation": "2.00%",
                "model_name": "Growth",
                "y1_service": {"pounds": "1014.06", "percent": "1.01"},
                "y1_asset": {"pounds": "285.82", "percent": "0.29"},
                "y1_total": {"pounds": "1299.88", "percent": "1.30"},
                "charge_rows": [],
                "tier_basis": "all of this client's assets on the platform",
                "other_assets": None,
                "tiers": [],
                "dfm_rate": "0.20%",
                "product_rate": "0.00%",
                "cash_paid": "0.00%",
                "cash_retained": "0.00%",
                "adviser_initial": "none",
                "adviser_ongoing": "0.50%",
                "cost_date": "2026-09-01",
                "assets": [],
                "weighted_oneoff": "0.00%",
                "weighted_ongoing": "0.24%",
                "weighted_incidental": "0.00%",
                "weighted_transaction": "0.04%",
                "table": [],
                "before_final": "£130,298",
                "after_all_final": "£107,827",
                "riy_product_text": None,
                "riy_all_text": "charges could have the same effect",
                "warnings": [],
                "legal_name": "Select Advice Ltd",
                "fca": "123456",
                "company_number": "11223344",
                "registered_office": "1 Test Street",
                "custodian_name": "SEI",
                "custodian_fca": "999999",
                "brand_color": "#0b3d2e",
                "target_return": "4.00%",
                "growth_spread": "3 percentage points",
            }
        },
    )
    assert "GIA Illustration" in html
    assert "£107,827" in html
    assert "Select Advice Ltd" in html
    assert "not a reliable indicator of future performance" in html
    assert "Growth rate a year" in html
    assert "-1.22%" in html
    assert "1.78%" in html
    assert "4.78%" in html
    assert "#0b3d2e" in html
    assert "4.00%" in html
    assert "asset-class" not in html
    assert "Liberation Serif" not in TEMPLATE_CSS
    assert "break-inside: avoid" in TEMPLATE_CSS


@pytest.mark.asyncio
async def test_unknown_client_is_404(maker) -> None:
    async with _svc(maker) as sess:
        with pytest.raises(HTTPException) as ei:
            await illustrations.create_investment_illustration(
                10_000_000, illustrations.InvestmentIllustrationInput(), _principal(), sess
            )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_a_missing_fact_files_nothing(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants (uid, alias, name, gia_isa_illustrations) "
                "VALUES (:u, :a, :n, true) RETURNING id",
                u=f"iv_{s}",
                a=f"iv-{s}",
                n="Firm",
            )
        )
        client = int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, email, name, dob) "
                "VALUES (:t, :u, 'individual', :e, 'Bo Client', '1988-05-27') RETURNING id",
                t=tenant,
                u=f"c_{s}",
                e=f"b_{s}@t.test",
            )
        )
        with pytest.raises(HTTPException) as ei:
            await illustrations.create_investment_illustration(
                client,
                illustrations.InvestmentIllustrationInput(
                    wrapper="GIA", start_date=date.today(), model_id=1
                ),
                _principal(),
                sess,
            )
        assert ei.value.status_code == 400
        assert "contribution" in str(ei.value.detail).lower()
        count = await _scalar(
            sess, "SELECT count(*) FROM platform.illustrations WHERE tenant_id = :t", t=tenant
        )
    assert count == 0


@pytest.mark.asyncio
async def test_a_complete_request_stores_the_snapshot(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants ("
                "  uid, alias, name, gia_isa_illustrations, legal_name, frn, chn, "
                "  registered_office, illustration_warning"
                ") VALUES ("
                "  :u, :a, 'Firm', true, 'Firm Ltd', 123456, '11223344', "
                "  '1 Test Street', 'Forecasts are not a reliable indicator of future performance.'"
                ") RETURNING id",
                u=f"iv_{s}",
                a=f"iv-{s}",
            )
        )
        client = int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, email, name) "
                "VALUES (:t, :u, 'individual', :e, 'Bo Client') RETURNING id",
                t=tenant,
                u=f"c_{s}",
                e=f"b_{s}@t.test",
            )
        )
        model = int(
            await _scalar(
                sess,
                "INSERT INTO platform.models (tenant_id, provider, name, target_return) "
                "VALUES (:t, 'seccl', 'Growth', 4) RETURNING id",
                t=tenant,
            )
        )
        await sess.execute(
            text(
                "INSERT INTO platform.fee_packages "
                "(tenant_id, name, category, frequency, fee_type, rule) "
                "VALUES (:t, 'Platform', 'Platform', 'ongoing', 'percentage', "
                "        CAST(:rule AS jsonb))"
            ),
            {"t": tenant, "rule": '{"rate": 0.30}'},
        )
        from app.wrapper_illustrations import MoneyFlowIn

        out = await illustrations.create_investment_illustration(
            client,
            illustrations.InvestmentIllustrationInput(
                wrapper="GIA",
                start_date=date.today(),
                term_years=10,
                model_id=model,
                adviser_ongoing=Decimal("0.005"),
                contributions=[MoneyFlowIn(type="single", amount=Decimal(100000), on=date.today())],
            ),
            _principal(),
            sess,
        )
        assert out.stored is True
        assert out.status == "generating"
        row = (
            (
                await sess.execute(
                    text(
                        "SELECT reference_snapshot, outputs, inputs FROM platform.illustrations "
                        "WHERE uid = :u"
                    ),
                    {"u": out.illustration_uid},
                )
            )
            .mappings()
            .one()
        )
        assert row["reference_snapshot"]["template_version"]
        assert row["reference_snapshot"]["fee_package"]["name"] == "Platform"
        assert Decimal(row["reference_snapshot"]["growth"]["mid"]) == Decimal("0.02")
        assert row["outputs"]["projected_value_mid"]
        assert row["inputs"]["product_type"] == "GIA"
        assert row["outputs"]["display"]["dob"] == "—"
        clock = row["outputs"]["display"]["prepared_at"].rsplit(", ", 1)[-1]
        assert len(clock) == 5 and clock[2] == ":"
        assert row["outputs"]["display"]["target_return"] == "4.00%"
        assert row["outputs"]["display"]["growth_spread"] == "3 percentage points"
        assert row["outputs"]["display"]["ink_color"] == DEFAULT_INK
        assert row["outputs"]["display"]["brand_color"] == DEFAULT_ACCENT
        assert row["outputs"]["display"]["logo"] is None
        assert row["reference_snapshot"]["brand"] == {
            "color": DEFAULT_ACCENT,
            "ink": DEFAULT_INK,
            "has_logo": False,
        }
        queued = await _scalar(
            sess,
            "SELECT status FROM documents.documents WHERE uid = :u",
            u=out.document_uid,
        )
        assert queued == "queued"


@pytest.mark.asyncio
async def test_a_missing_target_or_fee_writes_nothing(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants ("
                "  uid, alias, name, gia_isa_illustrations, legal_name, frn, chn, "
                "  registered_office, illustration_warning"
                ") VALUES ("
                "  :u, :a, 'Firm', true, 'Firm Ltd', 123456, '11223344', "
                "  '1 Test Street', 'Forecasts are not a reliable indicator of future performance.'"
                ") RETURNING id",
                u=f"iv_{s}",
                a=f"iv-{s}",
            )
        )
        client = int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, email, name, dob) "
                "VALUES (:t, :u, 'individual', :e, 'Bo Client', '1988-05-27') RETURNING id",
                t=tenant,
                u=f"c_{s}",
                e=f"b_{s}@t.test",
            )
        )
        model = int(
            await _scalar(
                sess,
                "INSERT INTO platform.models (tenant_id, provider, name, model_charge) "
                "VALUES (:t, 'sei', 'Growth', 0.20) RETURNING id",
                t=tenant,
            )
        )
        await sess.execute(
            text(
                "INSERT INTO platform.model_versions (tenant_id, model_id, version, published_at) "
                "VALUES (:t, :m, 1, now())"
            ),
            {"t": tenant, "m": model},
        )
        from app.wrapper_illustrations import MoneyFlowIn

        body = illustrations.InvestmentIllustrationInput(
            wrapper="GIA",
            start_date=date.today(),
            model_id=model,
            contributions=[MoneyFlowIn(type="single", amount=Decimal(1000), on=date.today())],
        )
        with pytest.raises(HTTPException) as missing_target:
            await illustrations.create_investment_illustration(client, body, _principal(), sess)
        assert missing_target.value.status_code == 400
        assert "target return" in str(missing_target.value.detail).lower()
        await sess.execute(
            text("UPDATE platform.models SET target_return = 5 WHERE id = :m"),
            {"m": model},
        )
        with pytest.raises(HTTPException) as missing_fee:
            await illustrations.create_investment_illustration(client, body, _principal(), sess)
        assert missing_fee.value.status_code == 400
        assert "platform fee" in str(missing_fee.value.detail).lower()
        count = await _scalar(
            sess, "SELECT count(*) FROM platform.illustrations WHERE tenant_id = :t", t=tenant
        )
    assert count == 0
