"""storage credentials: per-user object-storage credential registry

Revision ID: 0028_storage_credentials
Revises: 0027_tenant_storage
Create Date: 2026-07-17

The DDL lives in the sibling SQL file (migrations/sql/0028_storage_credentials.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_storage_credentials"
down_revision: str | None = "0027_tenant_storage"
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
    _run("0028_storage_credentials.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS storage.user_credential_secrets")
    op.execute("DROP TABLE IF EXISTS storage.user_credentials")
