"""compliance check alerted_at: when a monitoring alert last reopened a check

Revision ID: 0254_compliance_check_alerted_at
Revises: 0253_aml_rescreen_on_change
Create Date: 2026-10-13

``alembic_version.version_num`` is ``varchar(32)``; this id is 32 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0254_compliance_check_alerted_at"
down_revision: str | None = "0253_aml_rescreen_on_change"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0254_compliance_check_alerted_at.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE compliance.checks DROP COLUMN IF EXISTS alerted_at;
        """
    )
