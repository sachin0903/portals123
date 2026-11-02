"""Ad hoc fee carries which of the firm's fee packages it is charged under

Revision ID: 0172_instruction_fee_type
Revises: 0171_instruction_rail_fee
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0172_instruction_fee_type"
down_revision: str | None = "0171_instruction_rail_fee"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0172_instruction_fee_type.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS platform.instructions_fee_package_id_idx;
        ALTER TABLE platform.instructions
          DROP CONSTRAINT IF EXISTS instructions_fee_package_is_a_fee,
          DROP CONSTRAINT IF EXISTS instructions_fee_package_tenant_fkey,
          DROP COLUMN IF EXISTS fee_package_id;
        """
    )
