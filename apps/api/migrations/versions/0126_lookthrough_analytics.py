"""richer IFSL look-through analytics

Revision ID: 0126_lookthrough_analytics
Revises: 0124_ifsl_fund_lookthrough
Create Date: 2026-09-09
"""

from pathlib import Path

from alembic import op

revision: str = "0126_lookthrough_analytics"
down_revision: str | None = "0124_ifsl_fund_lookthrough"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0126_lookthrough_analytics.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TABLE IF EXISTS lookthrough.currency_contracts;
        DROP TABLE IF EXISTS lookthrough.cash_balances;
        DROP TABLE IF EXISTS lookthrough.nav_history;
        ALTER TABLE lookthrough.positions
          DROP COLUMN IF EXISTS movement_percent,
          DROP COLUMN IF EXISTS prior_price;
        """
    )
