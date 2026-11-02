"""Drop platform.provider_instructions -- a finished queue with no producer

Revision ID: 0077_drop_provider_instructions
Revises: 0076_merge_runs
Create Date: 2026-08-10

Contract phase. See the SQL file for why, and for what stays behind (the `ActionDef.retry`
classification, which is a fact about each vendor operation rather than about the transport).

The downgrade re-runs 0074's DDL rather than recreating it by hand, so the two cannot drift.
"""

from pathlib import Path

from alembic import op

revision: str = "0077_drop_provider_instructions"
down_revision: str | None = "0076_merge_runs"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0077_drop_provider_instructions.sql")


def downgrade() -> None:
    _run("0074_provider_instructions.sql")
