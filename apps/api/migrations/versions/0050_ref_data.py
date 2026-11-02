"""ref: global currencies and countries

Revision ID: 0050_ref_data
Revises: 0049_enable_active_providers
Create Date: 2026-08-02

The DDL and seed live in the sibling SQL file -- that file is the reviewable artifact; this
script just applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0050_ref_data"
down_revision: str | None = "0049_enable_active_providers"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0050_ref_data.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref.countries")
    op.execute("DROP TABLE IF EXISTS ref.currencies")
    # The schema goes only if nothing else has claimed it since.
    op.execute("DROP SCHEMA IF EXISTS ref RESTRICT")
