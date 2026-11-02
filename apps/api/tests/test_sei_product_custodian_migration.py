"""0259 connects an already-enabled SEI firm without waiting for a product save.

The shelf and the action runner both require product_platforms.role = 'custodian'.
A template save had inserted the row with a null role, and enabling SEI had
inserted nothing. Run as SQL, because that is what staging and production apply.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SQL = (
    Path(__file__).resolve().parent.parent / "migrations/sql/0259_sei_product_custodian.sql"
).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_migration_fills_a_null_role_and_connects_sei(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    suffix = uuid4().hex[:8]
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"sc{suffix}"[:12], "a": f"sc-{suffix}", "n": f"Shelf {suffix}"},
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled) "
            "VALUES (:t, 'sei', '{\"firm_id\": \"3108\"}'::jsonb, true)"
        ),
        {"t": tenant},
    )
    gia = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, product_type, name) "
                    "VALUES (:t, 'GIA', 'General') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )
    bond = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, product_type, name) "
                    "VALUES (:t, 'TPONBOND', 'Bond') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, account_open_reference, enabled) "
            "VALUES (:t, :p, 'sei', '157', '2981399', true)"
        ),
        {"t": tenant, "p": gia},
    )
    contact = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Nwosu') RETURNING id"
                ),
                {"t": tenant, "u": f"m{suffix}"[:12]},
            )
        ).scalar_one()
    )
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, uid, contact_id, product_id, name, status) "
                    "VALUES (:t, :u, :c, :p, 'Nwosu GIA', 'draft') RETURNING id"
                ),
                {"t": tenant, "u": uuid4().hex[:12], "c": contact, "p": gia},
            )
        ).scalar_one()
    )

    connection = await sess.connection()
    await connection.exec_driver_sql(_SQL)
    await sess.execute(text("SET LOCAL ROLE service_role"))

    gia_row = (
        (
            await sess.execute(
                text(
                    "SELECT role, external_id, account_open_reference, enabled "
                    "FROM platform.product_platforms WHERE product_id = :p AND provider = 'sei'"
                ),
                {"p": gia},
            )
        )
        .mappings()
        .one()
    )
    assert gia_row["role"] == "custodian"
    assert gia_row["external_id"] == "157"
    assert gia_row["account_open_reference"] == "2981399"
    assert gia_row["enabled"] is True
    bond_count = (
        await sess.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE product_id = :p AND provider = 'sei'"
            ),
            {"p": bond},
        )
    ).scalar_one()
    assert int(bond_count) == 0
    custodian = (
        await sess.execute(
            text("SELECT custodian::text FROM platform.accounts WHERE id = :a"),
            {"a": account},
        )
    ).scalar_one()
    assert custodian == "sei"

    await connection.exec_driver_sql(_SQL)
    await sess.execute(text("SET LOCAL ROLE service_role"))
    again = (
        await sess.execute(
            text("SELECT custodian::text FROM platform.accounts WHERE id = :a"),
            {"a": account},
        )
    ).scalar_one()
    assert again == "sei"
