"""page views: path-only navigations, separate from action events

Revision ID: 0213_page_views
Revises: 0212_mi_reports
Create Date: 2026-10-02

``alembic_version.version_num`` is ``varchar(32)``; this id is 16 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0213_page_views"
down_revision: str | None = "0212_mi_reports"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0213_page_views.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS audit.page_views;
        """
    )
