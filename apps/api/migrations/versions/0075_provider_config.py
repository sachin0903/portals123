"""platform.providers.config -- the vendor's per-connection reference values

Revision ID: 0075_provider_config
Revises: 0074_provider_instructions
Create Date: 2026-08-09

Expand phase; DDL in the sibling SQL file. The column defaults to `{}` and env stays the
fallback, so an unfilled row behaves exactly as today. Design note in the SQL.
"""

from pathlib import Path

from alembic import op

revision: str = "0075_provider_config"
down_revision: str | None = "0074_provider_instructions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0075_provider_config.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE platform.providers DROP COLUMN IF EXISTS config")
