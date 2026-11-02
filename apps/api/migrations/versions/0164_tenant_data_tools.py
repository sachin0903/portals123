"""Tenant data reset jobs and provider refresh pause

Revision ID: 0164_tenant_data_tools
Revises: 0163_listings_export
Create Date: 2026-09-14
"""

from pathlib import Path

from alembic import op

revision: str = "0164_tenant_data_tools"
down_revision: str | None = "0163_listings_export"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0164_tenant_data_tools.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TABLE IF EXISTS platform.tenant_data_jobs;
        ALTER TABLE platform.tenant_platforms
          DROP COLUMN IF EXISTS refresh_pause_reason,
          DROP COLUMN IF EXISTS refresh_paused_at;
        """
    )
