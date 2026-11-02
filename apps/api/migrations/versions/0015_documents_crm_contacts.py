"""bind document collaboration + requests to crm.contacts (drop the interim auth.users binding)

Revision ID: 0015_documents_crm_contacts
Revises: 0014_audit_append_scope
Create Date: 2026-07-15

Reconciles documents.document_contacts + documents.requests off the interim auth.users binding
(0006/0008) onto the true client model crm.contacts, with client-visibility RLS via
auth.contact_scope(). DDL in the sibling SQL file. The interim links were fictitious dev data and
are dropped, so the downgrade restores the interim shape but not any data.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_documents_crm_contacts"
down_revision: str | None = "0014_audit_append_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0015_documents_crm_contacts.sql")


def downgrade() -> None:
    # Restore the interim auth.users binding (schema only; the reconciled links are not reverted).
    op.execute("DROP POLICY documents_read ON documents.documents")
    op.execute("DROP TABLE documents.document_contacts")
    op.execute(
        "CREATE TABLE documents.document_contacts ("
        " document_id uuid NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,"
        " tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,"
        " user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,"
        " role text NOT NULL DEFAULT 'recipient',"
        " added_by uuid REFERENCES auth.users(id),"
        " added_at timestamptz NOT NULL DEFAULT now(),"
        " PRIMARY KEY (document_id, user_id, role))"
    )
    op.execute("CREATE INDEX document_contacts_user_id_idx ON documents.document_contacts(user_id)")
    op.execute("ALTER TABLE documents.document_contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents.document_contacts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_contacts_read ON documents.document_contacts FOR SELECT "
        "TO authenticated USING (user_id = auth.uid() OR tenant_id = ANY (auth.tenant_lineage()))"
    )
    op.execute(
        "CREATE POLICY document_contacts_write ON documents.document_contacts FOR ALL "
        "TO authenticated USING (tenant_id = ANY (auth.tenant_lineage())) "
        "WITH CHECK (tenant_id = ANY (auth.tenant_lineage()))"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_contacts TO authenticated"
    )
    op.execute("GRANT ALL ON documents.document_contacts TO service_role")
    op.execute(
        "CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING ("
        " tenant_id = ANY (auth.tenant_lineage()) OR EXISTS ("
        "   SELECT 1 FROM documents.document_contacts dc"
        "   WHERE dc.document_id = documents.documents.id AND dc.user_id = auth.uid()))"
    )
    op.execute("DROP POLICY requests_read ON documents.requests")
    op.execute("DROP POLICY request_items_read ON documents.request_items")
    op.execute("TRUNCATE documents.requests CASCADE")
    op.execute(
        "ALTER TABLE documents.requests DROP COLUMN contact_id"
    )  # drops requests_contact_idx
    op.execute(
        "ALTER TABLE documents.requests "
        "ADD COLUMN user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX requests_user_idx ON documents.requests(user_id, status)")
    op.execute(
        "CREATE POLICY requests_read ON documents.requests FOR SELECT TO authenticated "
        "USING (tenant_id = ANY (auth.tenant_lineage()) OR user_id = auth.uid())"
    )
    op.execute(
        "CREATE POLICY request_items_read ON documents.request_items FOR SELECT TO authenticated "
        "USING (tenant_id = ANY (auth.tenant_lineage()) OR EXISTS ("
        "  SELECT 1 FROM documents.requests r WHERE r.id = request_id AND r.user_id = auth.uid()))"
    )
    op.execute("DROP POLICY form_responses_read ON documents.form_responses")
    op.execute("ALTER TABLE documents.form_responses DROP COLUMN contact_id")
    op.execute(
        "ALTER TABLE documents.form_responses "
        "ADD COLUMN user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE POLICY form_responses_read ON documents.form_responses FOR SELECT TO authenticated "
        "USING (tenant_id = ANY (auth.tenant_lineage()) OR user_id = auth.uid())"
    )
