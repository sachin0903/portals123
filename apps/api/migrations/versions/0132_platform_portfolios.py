"""platform.portfolios: the container below an account

Revision ID: 0132_platform_portfolios
Revises: 0131_staff_profile
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0132_platform_portfolios"
down_revision: str | None = "0131_staff_profile"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0132_platform_portfolios.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        -- The DROP COLUMN covers a database where the first cut of this migration ran: it
        -- added `accounts.default_portfolio_id`, which the SQL no longer does (see there).
        DROP TABLE IF EXISTS platform.portfolios CASCADE; ALTER TABLE platform.accounts DROP COLUMN IF EXISTS default_portfolio_id;
        """
    )
