"""Product custody choices and kickoff party limit

Revision ID: 0160_product_kickoff_rules
Revises: 0159_contacts_create
Create Date: 2026-09-14
"""

from pathlib import Path

from alembic import op

revision: str = "0160_product_kickoff_rules"
down_revision: str | None = "0159_contacts_create"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0160_product_kickoff_rules.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.products
          DROP CONSTRAINT IF EXISTS products_max_parties_positive,
          DROP CONSTRAINT IF EXISTS products_allowed_custodies_not_empty,
          DROP COLUMN IF EXISTS max_parties,
          DROP COLUMN IF EXISTS allowed_custodies;
        """
    )
