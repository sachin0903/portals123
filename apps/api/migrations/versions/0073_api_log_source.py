"""audit.api_log.source -- app vs sync, and the book_writer INSERT grant

Revision ID: 0073_api_log_source
Revises: 0072_merge_pipeline_and_notify
Create Date: 2026-08-09

Expand phase; DDL in the sibling SQL file. The column takes a default, so nothing has to be
backfilled -- and nothing could be: the table is empty, because 0047's pipeline has never had a
producer (docs/provider-routing.md §6).
"""

from pathlib import Path

from alembic import op

revision: str = "0073_api_log_source"
down_revision: str | None = "0072_merge_pipeline_and_notify"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0073_api_log_source.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("REVOKE INSERT ON audit.api_log FROM book_writer")
    op.get_bind().exec_driver_sql("DROP INDEX IF EXISTS audit.api_log_source_idx")
    op.get_bind().exec_driver_sql("ALTER TABLE audit.api_log DROP COLUMN IF EXISTS source")
