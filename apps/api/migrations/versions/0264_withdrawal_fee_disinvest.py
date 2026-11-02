"""an adviser fee and an auto-disinvest can ride a withdrawal, not just a payment

Revision ID: 0264_withdrawal_fee_disinvest
Revises: 0263_transaction_money_namespace
Create Date: 2026-10-19

Relaxes the adviser-fee payment-only constraint to allow withdrawal kinds (renamed
instructions_adviser_fee_movement_only), and adds platform.instructions.auto_disinvest (the
"sell down to fund this" option), constrained to the money-out kinds.
"""

from pathlib import Path

from alembic import op

revision: str = "0264_withdrawal_fee_disinvest"
down_revision: str | None = "0263_transaction_money_namespace"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0264_withdrawal_fee_disinvest.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.instructions\n"
        "  DROP CONSTRAINT instructions_auto_disinvest_withdrawal_only,\n"
        "  DROP COLUMN auto_disinvest,\n"
        "  DROP CONSTRAINT instructions_adviser_fee_movement_only,\n"
        "  ADD CONSTRAINT instructions_adviser_fee_payment_only\n"
        "    CHECK (adviser_fee = 0 OR kind = 'expected_in');"
    )
