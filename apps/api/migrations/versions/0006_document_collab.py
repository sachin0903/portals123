"""document collaboration: client links (bound to auth.users, interim)

Revision ID: 0006_document_collab
Revises: 0005_documents_storage
Create Date: 2026-07-09

Adviser<->client collaboration for the documents module: documents.document_contacts (client
links), plus the client-visibility RLS branch on documents/files. Bound to auth.users for now
(crm.contacts isn't built) — see the SQL header. The DDL lives in the sibling SQL file; this
script just applies it, as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_document_collab"
down_revision: str | None = "0005_documents_storage"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0006_document_collab.sql")


def downgrade() -> None:
    # Drop the client-links table and restore the staff-only reads from 0002.
    op.execute("DROP TABLE IF EXISTS documents.document_contacts CASCADE")
    op.execute("DROP POLICY IF EXISTS documents_read ON documents.documents")
    op.execute(
        "CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated "
        "USING (tenant_id = ANY (auth.tenant_lineage()))"
    )
    op.execute("DROP POLICY IF EXISTS documents_files_read ON documents.files")
    op.execute(
        "CREATE POLICY documents_files_read ON documents.files FOR SELECT TO authenticated "
        "USING (tenant_id = ANY (auth.tenant_lineage()))"
    )
