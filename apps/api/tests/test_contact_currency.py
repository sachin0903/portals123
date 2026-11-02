"""An unknown custodian currency is stored as sterling, not a failed merge."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_service_role_stores_an_unknown_currency_as_sterling(
    service_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"ccy_{suffix}", "a": f"ccy-{suffix}", "n": f"Currency {suffix}"},
            )
        ).scalar_one()
    )
    stored = (
        await service_session.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id, uid, type, name, email, currency) "
                "VALUES (:t, :u, 'individual', 'Ada', :e, 'NOPE') RETURNING currency"
            ),
            {"t": tenant, "u": f"k{suffix}0000"[:12], "e": f"ada.{suffix}@example.com"},
        )
    ).scalar_one()
    assert stored == "GBP"


@pytest.mark.asyncio
async def test_a_known_currency_is_kept(service_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"cc2_{suffix}", "a": f"cc2-{suffix}", "n": f"Currency {suffix}"},
            )
        ).scalar_one()
    )
    stored = (
        await service_session.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id, uid, type, name, email, currency) "
                "VALUES (:t, :u, 'individual', 'Ada', :e, 'USD') RETURNING currency"
            ),
            {"t": tenant, "u": f"u{suffix}0000"[:12], "e": f"ada.{suffix}@example.com"},
        )
    ).scalar_one()
    assert stored == "USD"
