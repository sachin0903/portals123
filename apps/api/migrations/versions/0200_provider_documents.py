"""File a custodian document into a tenant's library

Revision ID: 0200_provider_documents
Revises: 0199_account_open_reference
Create Date: 2026-09-22

First applied as ``0182_provider_documents``, then as ``0191_provider_documents``.
"""

from pathlib import Path

from alembic import op

revision: str = "0200_provider_documents"
down_revision: str | None = "0199_account_open_reference"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


_SIGNATURE = "int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb"


def upgrade() -> None:
    sql = (_SQL_DIR / "0200_provider_documents.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        f"""
        DROP FUNCTION IF EXISTS documents.ingest_provider_document({_SIGNATURE});
        DROP INDEX IF EXISTS documents.documents_provider_external_key;
        ALTER TABLE documents.documents DROP CONSTRAINT IF EXISTS documents_source_check;
        ALTER TABLE documents.documents
          ADD CONSTRAINT documents_source_check
          CHECK (source IN ('generated', 'uploaded'));
        """
    )
