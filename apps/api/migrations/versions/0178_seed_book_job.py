"""seed_book: replace the demo client book without a full tenant reset

Revision ID: 0178_seed_book_job
Revises: 0177_products_client_read
Create Date: 2026-09-21
"""

from pathlib import Path

from alembic import op

revision: str = "0178_seed_book_job"
down_revision: str | None = "0177_products_client_read"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0178_seed_book_job.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE platform.tenant_data_jobs
          DROP CONSTRAINT IF EXISTS tenant_data_jobs_kind_check;
        ALTER TABLE platform.tenant_data_jobs
          ADD CONSTRAINT tenant_data_jobs_kind_check
          CHECK (kind IN ('reset', 'reset_and_seed'));
        """
    )
