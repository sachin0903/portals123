"""platform.platforms: UNIQUE(provider), so the registry can be upserted on startup

Revision ID: 0055_platforms_unique_provider
Revises: 0054_contact_vulnerability
Create Date: 2026-08-03

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0055_platforms_unique_provider"
down_revision: str | None = "0054_contact_vulnerability"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0055_platforms_unique_provider.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.platforms DROP CONSTRAINT IF EXISTS platforms_provider_key")
