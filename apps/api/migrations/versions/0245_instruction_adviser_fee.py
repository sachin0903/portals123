"""Optional adviser fee on a payment-in instruction

Revision ID: 0245_instruction_adviser_fee
Revises: 0244_merge_seeded_form_lists
Create Date: 2026-10-08
"""

from pathlib import Path

from alembic import op

revision: str = "0245_instruction_adviser_fee"
down_revision: str | None = "0244_merge_seeded_form_lists"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0245_instruction_adviser_fee.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.instructions
          DROP CONSTRAINT IF EXISTS instructions_adviser_fee_payment_only,
          DROP CONSTRAINT IF EXISTS instructions_adviser_fee_nonnegative,
          DROP COLUMN IF EXISTS adviser_fee;
        """
    )
