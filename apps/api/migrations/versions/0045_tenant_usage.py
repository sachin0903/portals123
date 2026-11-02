"""storage: cached object-storage totals per tenant

Revision ID: 0045_tenant_usage
Revises: 0044_products_multi_platform
Create Date: 2026-08-02

The DDL lives in the sibling SQL file (migrations/sql/0045_tenant_usage.sql) -- that file is
the reviewable artifact; this script just applies it. Runs as the elevated migrator role (see
migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045_tenant_usage"
down_revision: str | None = "0044_products_multi_platform"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0045_tenant_usage.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS storage.tenant_usage")
