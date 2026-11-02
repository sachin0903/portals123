"""A firm's currency allow-list and base/default are one validated setting."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.catalogue import upsert_product
from app.provisioning import provision_tenant
from app.routers.admin import (
    SetTenantCurrencies,
    get_tenant_currency_settings,
    set_tenant_currency_settings,
)

_SYSTEM = Principal(user_id="currency_settings_ops", permissions=["system"])


@pytest.mark.asyncio
async def test_new_firm_starts_gbp_only_and_can_replace_its_currency_policy(
    service_session: AsyncSession,
) -> None:
    tenant = await provision_tenant(service_session, name="Currency Firm", alias="currency-firm")
    tenant_id = int(tenant["id"])

    initial = await get_tenant_currency_settings(
        tenant_id=tenant_id, session=service_session, principal=_SYSTEM
    )
    assert initial["base_currency"] == "GBP"
    assert initial["allowed_currencies"] == ["GBP"]

    updated = await set_tenant_currency_settings(
        SetTenantCurrencies(base_currency="EUR", allowed_currencies=["EUR", "GBP", "USD"]),
        tenant_id=tenant_id,
        session=service_session,
        principal=_SYSTEM,
    )
    assert updated["base_currency"] == "EUR"
    assert updated["allowed_currencies"] == ["EUR", "GBP", "USD"]
    reference_codes = [row["code"] for row in updated["currencies"]]
    assert reference_codes[0] == "EUR"
    assert {"EUR", "GBP", "USD"} <= set(reference_codes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base", "allowed", "message"),
    [
        ("GBP", ["EUR"], "base currency"),
        ("GBP", ["GBP", "GBP"], "once"),
        ("AAA", ["AAA"], "Unknown currency"),
    ],
)
async def test_currency_policy_refuses_incoherent_or_unknown_choices(
    service_session: AsyncSession,
    base: str,
    allowed: list[str],
    message: str,
) -> None:
    tenant = await provision_tenant(
        service_session, name=f"Bad Currency {message}", alias=f"bad-{base.lower()}-{len(allowed)}"
    )
    with pytest.raises(HTTPException) as exc:
        await set_tenant_currency_settings(
            SetTenantCurrencies(base_currency=base, allowed_currencies=allowed),
            tenant_id=int(tenant["id"]),
            session=service_session,
            principal=_SYSTEM,
        )
    assert exc.value.status_code == 422
    assert message.lower() in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_database_refuses_an_unknown_allowed_currency(
    service_session: AsyncSession,
) -> None:
    tenant = await provision_tenant(
        service_session, name="Currency Boundary", alias="currency-boundary"
    )
    with pytest.raises(IntegrityError):
        async with service_session.begin_nested():
            await service_session.execute(
                text(
                    "UPDATE auth.tenants SET allowed_currencies = ARRAY['GBP', 'AAA'] WHERE id = :t"
                ),
                {"t": int(tenant["id"])},
            )


@pytest.mark.asyncio
async def test_firm_currency_cannot_be_removed_while_a_product_uses_it(
    service_session: AsyncSession,
) -> None:
    tenant = await provision_tenant(
        service_session, name="Product Currency Boundary", alias="product-currency-boundary"
    )
    tenant_id = int(tenant["id"])
    await set_tenant_currency_settings(
        SetTenantCurrencies(base_currency="GBP", allowed_currencies=["GBP", "EUR"]),
        tenant_id=tenant_id,
        session=service_session,
        principal=_SYSTEM,
    )
    await upsert_product(
        service_session,
        tenant_id,
        {
            "product_type": "GIA",
            "name": "Euro GIA",
            "allowed_currencies": ["GBP", "EUR"],
        },
    )

    with pytest.raises(HTTPException, match="Euro GIA"):
        await set_tenant_currency_settings(
            SetTenantCurrencies(base_currency="GBP", allowed_currencies=["GBP"]),
            tenant_id=tenant_id,
            session=service_session,
            principal=_SYSTEM,
        )
