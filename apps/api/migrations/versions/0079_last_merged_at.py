"""platform.tenant_platforms.last_merged_at -- the fan-out column, renamed for what stamps it

Revision ID: 0079_last_merged_at
Revises: 0078_tenant_platforms_comment
Create Date: 2026-08-10

Expand phase: both columns exist, the writer sets both, readers prefer the new one. The contract
phase drops `last_fanout_at` in a later change, once nothing reads it.
"""

from pathlib import Path

from alembic import op

revision: str = "0079_last_merged_at"
down_revision: str | None = "0078_tenant_platforms_comment"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0079_last_merged_at.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.tenant_platforms DROP COLUMN IF EXISTS last_merged_at"
    )
