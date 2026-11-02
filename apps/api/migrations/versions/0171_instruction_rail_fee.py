"""Reserve provider payment-rail fees on withdrawal instructions

Revision ID: 0171_instruction_rail_fee
Revises: 0170_tenant_currencies
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0171_instruction_rail_fee"
down_revision: str | None = "0170_tenant_currencies"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0171_instruction_rail_fee.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.instructions
          DROP CONSTRAINT IF EXISTS instructions_rail_fee_withdrawal_only,
          DROP CONSTRAINT IF EXISTS instructions_rail_fee_nonnegative,
          DROP COLUMN IF EXISTS rail_fee;
        """
    )
