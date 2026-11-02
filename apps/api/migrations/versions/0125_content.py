"""firm-published client portal content (news, articles, guides)

Revision ID: 0125_content
Revises: 0126_lookthrough_analytics
Create Date: 2026-09-09
"""

from pathlib import Path

from alembic import op

revision: str = "0125_content"
down_revision: str | None = "0126_lookthrough_analytics"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0125_content.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS content CASCADE;")
