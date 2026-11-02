"""platform.valuations: the crystallisation split, and cash held at the scheme administrator

Revision ID: 0130_valuation_pension_split
Revises: 0129_contact_kyc_facts
Create Date: 2026-09-04
"""

from pathlib import Path

from alembic import op

revision: str = "0130_valuation_pension_split"
down_revision: str | None = "0129_contact_kyc_facts"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0130_valuation_pension_split.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.valuations
          DROP COLUMN IF EXISTS crystallised_value,
          DROP COLUMN IF EXISTS uncrystallised_value;
        """
    )
