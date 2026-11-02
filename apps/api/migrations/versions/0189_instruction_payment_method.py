"""A withdrawal can choose its rail, and the firm can pay the fee

Revision ID: 0189_instruction_payment_method
Revises: 0188_journey_assignee
Create Date: 2026-09-28

The bank account stores the default (BACS or Faster Payment). The instruction records the
rail actually used, and whether the client or the firm bears the provider's charge.
"""

from pathlib import Path

from alembic import op

revision: str = "0189_instruction_payment_method"
down_revision: str | None = "0188_journey_assignee"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0189_instruction_payment_method.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.instructions
          DROP CONSTRAINT IF EXISTS instructions_payment_method_withdrawal_only,
          DROP CONSTRAINT IF EXISTS instructions_payment_method_known,
          DROP COLUMN IF EXISTS payment_method,
          DROP COLUMN IF EXISTS pass_rail_fee;
        """
    )
