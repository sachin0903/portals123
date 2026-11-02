"""platform.merge_runs -- the per-entity, per-run ledger of what the merge did

Revision ID: 0076_merge_runs
Revises: 0075_provider_config
Create Date: 2026-08-10

Expand phase: a new table, nothing backfilled and nothing to backfill. The counts it records are
produced by `book_sink.merge.merge_tenant` on every run and were, until now, returned as Arrow to
a warehouse relation holding one run and read by nobody.
"""

from pathlib import Path

from alembic import op

revision: str = "0076_merge_runs"
down_revision: str | None = "0075_provider_config"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0076_merge_runs.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS platform.merge_runs")
