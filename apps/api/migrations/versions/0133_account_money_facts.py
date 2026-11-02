"""platform.accounts: bank details and the wrapper allowance

Revision ID: 0133_account_money_facts
Revises: 0132_platform_portfolios
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0133_account_money_facts"
down_revision: str | None = "0132_platform_portfolios"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0133_account_money_facts.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.accounts DROP CONSTRAINT IF EXISTS accounts_bank_accounts_is_array, DROP COLUMN IF EXISTS bank_accounts, DROP COLUMN IF EXISTS allowance_used, DROP COLUMN IF EXISTS allowance_limit, DROP COLUMN IF EXISTS allowance_tax_year;
        """
    )
