"""Product owner contact-type eligibility

Revision ID: 0166_product_client_types
Revises: 0165_contact_phone_numbers
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0166_product_client_types"
down_revision: str | None = "0165_contact_phone_numbers"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0166_product_client_types.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.products
          DROP CONSTRAINT IF EXISTS products_allowed_client_types_not_empty,
          DROP COLUMN IF EXISTS allowed_client_types;
        """
    )
