"""global dated IFSL fund look-through data

Revision ID: 0124_ifsl_fund_lookthrough
Revises: 0123_messaging_multi_attachments
Create Date: 2026-09-09
"""

from pathlib import Path

from alembic import op

revision: str = "0124_ifsl_fund_lookthrough"
down_revision: str | None = "0123_messaging_multi_attachments"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0124_ifsl_fund_lookthrough.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS lookthrough CASCADE;")
