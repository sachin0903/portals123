"""platform.product_platforms: which provider does what for a product

Revision ID: 0135_product_platform_roles
Revises: 0134_contact_fatca
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0135_product_platform_roles"
down_revision: str | None = "0134_contact_fatca"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0135_product_platform_roles.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS platform.product_platforms_one_provider_per_role; ALTER TABLE platform.product_platforms DROP CONSTRAINT IF EXISTS product_platforms_role_check, DROP COLUMN IF EXISTS role, DROP COLUMN IF EXISTS required;
        """
    )
