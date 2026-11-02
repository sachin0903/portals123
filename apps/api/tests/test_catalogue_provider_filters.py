"""`product` (array overlap) and `rail` (EXISTS over unpacked jsonb) are new SQL with no other
coverage. Both mean "any of these", and the wrong operator returns rows matching ALL of them —
which looks plausible."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue


@pytest.mark.asyncio
async def test_product_and_rail_filters_mean_ANY_of_these(service_session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, 'F') RETURNING id"
                ),
                {"u": f"ten_{tag}"},
            )
        ).scalar_one()
    )
    for name, product, rail in (
        ("Aviva", "SIPP", "origo"),
        ("Quilter", "ISA", "atg"),
        ("No", "GIA", "manual"),
    ):
        await service_session.execute(
            text(
                "INSERT INTO platform.transfer_providers (tenant_id, name, products, rails) "
                "VALUES (:t, :n, CAST(:p AS text[]), CAST(:r AS jsonb))"
            ),
            {"t": tenant, "n": name, "p": [product], "r": f'[{{"rail":"{rail}"}}]'},
        )

    async def names(**kw: Any) -> set[str]:
        page = await catalogue.providers(service_session, tenant, "transfer", **kw)
        return {row["name"] for row in page["items"]}

    assert await names(product=["SIPP", "ISA"]) == {"Aviva", "Quilter"}
    assert await names(rail=["origo", "atg"]) == {"Aviva", "Quilter"}
    assert await names(product="SIPP") == {"Aviva"}  # a bare string is one value
