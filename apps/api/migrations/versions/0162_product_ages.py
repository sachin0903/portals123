"""Product owner age limits (JISA max 17)

Revision ID: 0162_product_ages
Revises: 0161_notification_dead_links
Create Date: 2026-09-14
"""

from pathlib import Path

from alembic import op

revision: str = "0162_product_ages"
down_revision: str | None = "0161_notification_dead_links"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0162_product_ages.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.products
          DROP CONSTRAINT IF EXISTS products_age_range,
          DROP CONSTRAINT IF EXISTS products_age_nonnegative,
          DROP COLUMN IF EXISTS max_age,
          DROP COLUMN IF EXISTS min_age;
        """
    )
