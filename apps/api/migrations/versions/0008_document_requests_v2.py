"""richer adviser->client requests: header + line items

Revision ID: 0008_document_requests_v2
Revises: 0007_document_requests
Create Date: 2026-07-09

Replaces documents.upload_requests with documents.requests + documents.request_items (multi-item,
multi-action). The DDL lives in the sibling SQL file; this script applies it as the elevated
migrator role. Bound to auth.users for now (interim) — see the SQL header.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_document_requests_v2"
down_revision: str | None = "0007_document_requests"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0008_document_requests_v2.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.request_items CASCADE")
    op.execute("DROP TABLE IF EXISTS documents.requests CASCADE")
