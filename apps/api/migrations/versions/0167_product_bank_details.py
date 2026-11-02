"""Product withdrawal bank-details eligibility

Revision ID: 0167_product_bank_details
Revises: 0166_product_client_types
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0167_product_bank_details"
down_revision: str | None = "0166_product_client_types"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0167_product_bank_details.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.products DROP COLUMN IF EXISTS bank_details_allowed;"
    )
