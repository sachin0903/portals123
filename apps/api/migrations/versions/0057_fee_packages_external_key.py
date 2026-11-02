"""fee_packages: UNIQUE(tenant_id, external_id), so the Seccl fee register can be synced

Revision ID: 0057_fee_packages_external_key
Revises: 0056_contribution_lifecycle
Create Date: 2026-08-04

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0057_fee_packages_external_key"
down_revision: str | None = "0056_contribution_lifecycle"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0057_fee_packages_external_key.sql")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.fee_packages_tenant_external_key")
    op.execute("COMMENT ON COLUMN platform.fee_packages.external_id IS NULL")
