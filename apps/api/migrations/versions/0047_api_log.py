"""outgoing provider calls: audit.api_log

Revision ID: 0047_api_log
Revises: 0046_transfer_provider_rails
Create Date: 2026-08-02

The DDL lives in the sibling SQL file -- that file is the reviewable artifact; this script just
applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0047_api_log"
down_revision: str | None = "0046_transfer_provider_rails"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0047_api_log.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.api_log")
