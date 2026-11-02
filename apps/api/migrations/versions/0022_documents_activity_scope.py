"""documents: book-scope the activity trail + requests, and un-stick client Unread

Revision ID: 0022_documents_activity_scope
Revises: 0021_documents_scope
Create Date: 2026-07-24

Extends 0021's adviser-book scoping to documents.audit_log (documents_audit_read) and
documents.requests/request_items — the overview activity feed leaked every tenant's audit rows
(and the client names in their `detail`) to book-scoped advisers, requests were unscoped, and a
client read back zero audit rows so their own Unread/Last-interacted state never updated. DDL in
the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_documents_activity_scope"
down_revision: str | None = "0021_documents_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the pre-0022 tenant-wide policies (audit from 0005, requests from 0015).
_DOWN = """
DROP POLICY documents_audit_read ON documents.audit_log;
CREATE POLICY documents_audit_read ON documents.audit_log FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY requests_read ON documents.requests;
CREATE POLICY requests_read ON documents.requests FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));

DROP POLICY request_items_read ON documents.request_items;
CREATE POLICY request_items_read ON documents.request_items FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
        SELECT 1 FROM documents.requests r
        WHERE r.id = request_id AND r.contact_id = ANY (auth.contact_scope())
    )
);

DROP POLICY requests_write ON documents.requests;
CREATE POLICY requests_write ON documents.requests FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY request_items_write ON documents.request_items;
CREATE POLICY request_items_write ON documents.request_items FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0022_documents_activity_scope.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
