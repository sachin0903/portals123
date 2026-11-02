"""Global switch for shipped management reports

Revision ID: 0212_mi_reports
Revises: 0211_search_column
Create Date: 2026-09-28
"""

from pathlib import Path

from alembic import op

revision: str = "0212_mi_reports"
down_revision: str | None = "0211_search_column"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0212_mi_reports.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS platform.mi_reports;
        """
    )
