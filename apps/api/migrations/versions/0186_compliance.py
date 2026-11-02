"""compliance schema: screening subjects, checks, and the provider mirror

Revision ID: 0186_compliance
Revises: 0185_txn_effective_date_idx
Create Date: 2026-09-24
"""

from pathlib import Path

from alembic import op

revision: str = "0186_compliance"
down_revision: str | None = "0185_txn_effective_date_idx"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0186_compliance.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP SCHEMA IF EXISTS compliance CASCADE;
        """
    )
