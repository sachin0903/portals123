"""document upload requests: adviser asks a client to upload

Revision ID: 0007_document_requests
Revises: 0006_document_collab
Create Date: 2026-07-09

documents.upload_requests + RLS. The DDL lives in the sibling SQL file; this script applies it as
the elevated migrator role. Bound to auth.users for now (interim) — see the SQL header.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_document_requests"
down_revision: str | None = "0006_document_collab"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0007_document_requests.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.upload_requests CASCADE")
