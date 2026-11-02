"""platform.provider_instructions -- the outbound instruction queue

Revision ID: 0074_provider_instructions
Revises: 0073_api_log_source
Create Date: 2026-08-09

Expand phase; DDL in the sibling SQL file. A new table with no readers yet, so nothing to
backfill. Design record: docs/provider-routing.md section 6a.
"""

from pathlib import Path

from alembic import op

revision: str = "0074_provider_instructions"
down_revision: str | None = "0073_api_log_source"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0074_provider_instructions.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS platform.provider_instructions")
