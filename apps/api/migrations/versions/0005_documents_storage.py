"""documents storage base: files + documents + product audit (staff-only RLS)

Revision ID: 0005_documents_storage
Revises: 0004_portal_scope
Create Date: 2026-07-09

The crm/platform-independent storage layer for document management, landed ahead of the
platform_crm migration (which adds the contact/account FKs, the contact-linked tables, sharing
via crm.shares, and the client-visible RLS branch). The DDL lives in the sibling SQL file
(migrations/sql/0005_documents_storage.sql) -- that file is the reviewable artifact; this script
just applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_documents_storage"
down_revision: str | None = "0004_portal_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg as a single multi-statement
    # script. psycopg uses pyformat, so double every `%` so `%%` collapses back to a literal `%`.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0005_documents_storage.sql")


def downgrade() -> None:
    # Greenfield: drop the whole documents schema (tables, enum, policies, the audit trigger).
    op.execute("DROP SCHEMA IF EXISTS documents CASCADE")
