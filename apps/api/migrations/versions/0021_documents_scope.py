"""documents: adviser book scoping + deferred client visibility until sent

Revision ID: 0021_documents_scope
Revises: 0026_kyc_checks
Create Date: 2026-07-24

Re-chained onto the flows engine head (0026_kyc_checks): both this branch and Craig's
document-UI work independently descended from 0020, producing two Alembic heads on merge.
The flows chain (0021_flows … 0026_kyc_checks) is already applied to the working DB, so it
keeps its revision IDs and these two document-scope migrations descend from it as pending work.
The filename prefixes (0021/0022) therefore no longer match topological order — the down_revision
chain is authoritative.

Two fixes: (1) documents_read now scopes book-restricted staff to their assigned contacts
(mirrors crm.contacts), instead of every staff member seeing every document in the tenant;
(2) a new `intended_contact_id` column replaces generate_document's immediate document_contacts
bind, so a client only sees a document once it's explicitly sent, not the moment generation is
queued. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_documents_scope"
down_revision: str | None = "0026_kyc_checks"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the pre-0021 policies (from 0015/0005) and drop the new column.
_DOWN = """
ALTER TABLE documents.documents DROP COLUMN intended_contact_id;

DROP POLICY documents_read ON documents.documents;
DROP POLICY documents_write ON documents.documents;

CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
        SELECT 1 FROM documents.document_contacts dc
        WHERE dc.document_id = documents.documents.id
          AND dc.contact_id = ANY (auth.contact_scope())
    )
);
CREATE POLICY documents_write ON documents.documents FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0021_documents_scope.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
