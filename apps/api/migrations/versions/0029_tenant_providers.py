"""tenant providers: integrations schema + per-tenant provider enablement

Revision ID: 0029_tenant_providers
Revises: 0028_storage_credentials
Create Date: 2026-07-19

The DDL lives in the sibling SQL file (migrations/sql/0029_tenant_providers.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_tenant_providers"
down_revision: str | None = "0028_storage_credentials"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg (no SQLAlchemy bind-param
    # parsing, so `::cast` is safe) as a single multi-statement script. psycopg uses
    # pyformat, so it still scans for `%` placeholders -- double every `%` (in RAISE/
    # format() bodies) so psycopg collapses `%%` back to a literal `%` for the server.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0029_tenant_providers.sql")


def downgrade() -> None:
    # The schema currently holds only the enablement registry; later connectivity
    # tables ship their own migrations + downgrades.
    op.execute("DROP SCHEMA IF EXISTS integrations CASCADE")
