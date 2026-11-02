"""a one-off fee is a cash instruction, like a withdrawal without a destination bank

Revision ID: 0154_instruction_adhoc_fee
Revises: 0153_contact_provider_status
Create Date: 2026-09-12
"""

from pathlib import Path

from alembic import op

revision: str = "0154_instruction_adhoc_fee"
down_revision: str | None = "0153_contact_provider_status"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0154_instruction_adhoc_fee.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.instructions DROP CONSTRAINT IF EXISTS instructions_kind_check;
        ALTER TABLE platform.instructions
          ADD CONSTRAINT instructions_kind_check
          CHECK (kind IN ('expected_in', 'withdrawal', 'model_topup', 'model_withdrawal'));
        """
    )
