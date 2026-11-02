"""files as a first-class entity (documents.attachments)

Revision ID: 0018_attachments
Revises: 0017_document_shares
Create Date: 2026-07-15

Supporting client files (passport, ID, proof of address) as their own entity, distinct from
financial documents. DDL in the sibling SQL file; applied as the elevated migrator role. See
docs/document-management.md.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_attachments"
down_revision: str | None = "0017_document_shares"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0018_attachments.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.attachments CASCADE")
