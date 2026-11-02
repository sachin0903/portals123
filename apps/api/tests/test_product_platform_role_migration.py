"""Regression coverage for the valid multi-custodian shape that broke staging at 0135."""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, text


def test_0135_does_not_enforce_one_provider_per_role_before_its_backfill() -> None:
    sql = (Path(__file__).parents[1] / "migrations/sql/0135_product_platform_roles.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE UNIQUE INDEX product_platforms_one_provider_per_role" not in sql


def test_one_shelf_product_can_be_available_from_two_custodians(super_engine: Engine) -> None:
    suffix = uuid4().hex[:8]
    with super_engine.connect() as connection:
        tenant_id = int(
            connection.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"mpr_{suffix}",
                    "alias": f"multi-provider-{suffix}",
                    "name": f"Multi-provider {suffix}",
                },
            ).scalar_one()
        )
        product_id = int(
            connection.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, product_type, code, name) "
                    "VALUES (:tenant, 'SIPP', 'SIPP', 'SIPP') RETURNING id"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
        )
        connection.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, external_id, role) "
                "VALUES (:tenant, :product, 'seccl', 'SEC-SIPP', 'custodian'), "
                "       (:tenant, :product, 'sei', 'SEI-SIPP', 'custodian')"
            ),
            {"tenant": tenant_id, "product": product_id},
        )
        roles = connection.execute(
            text(
                "SELECT provider::text, role FROM platform.product_platforms "
                "WHERE product_id = :product ORDER BY provider"
            ),
            {"product": product_id},
        ).all()
        connection.commit()

    assert roles == [("seccl", "custodian"), ("sei", "custodian")]
