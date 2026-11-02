"""compliance: when the worker last asked ComplyCube about a pending check

Revision ID: 0226_complycube_polled_at
Revises: 0225_drop_aml_schema
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0226_complycube_polled_at"
down_revision: str | None = "0225_drop_aml_schema"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0226_complycube_polled_at.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP INDEX IF EXISTS compliance.compliance_checks_pending_idx;
        DROP TABLE IF EXISTS compliance.check_polls;
        """
    )
