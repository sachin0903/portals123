"""book-scope the tables the audit found tenant-wide, on the one can_read_all/contact_scope idiom

Revision ID: 0031_book_scope_alignment
Revises: 0030_auth_directory_scope
Create Date: 2026-07-28

crm.kyc_checks, flows.cases/case_steps/executions/domain_events, documents.shares (tenant-wide
WITH FULL WRITE — a book-scoped adviser could revoke, and MINT, public share links for documents
they cannot see), documents.attachments and platform.instructions were tenant-wide only; tasks.tasks'
WITH CHECK was tenant-wide (book scoping there was application-code-only); crm.applications used a
fourth idiom (adviser_id = auth.uid()). All are brought onto the crm.contacts /
0021_documents_scope expression, in the USING *and* the WITH CHECK — "the router validates the
target first" does not hold at the DB, and for shares an out-of-book INSERT is a live public
download link. flows.executions' scope needs a SECURITY DEFINER accessor (the negative NOT EXISTS
shape is unsound once flows.cases is itself book-scoped); it guards its argument internally so it
cannot be called as a cross-tenant oracle. DDL in the sibling SQL file; applied as the elevated
migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_book_scope_alignment"
down_revision: str | None = "0030_auth_directory_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the pre-0031 policies: kyc_checks (0026), cases/case_steps (0021_flows),
# executions/domain_events (0025), shares (0017), attachments (0018), instructions (0016),
# tasks_write (0028) and applications_read (0002).
_DOWN = """
DROP POLICY kyc_checks_read ON crm.kyc_checks;
CREATE POLICY kyc_checks_read ON crm.kyc_checks FOR SELECT TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR contact_id = ANY (auth.contact_scope())
  );

DROP POLICY cases_read ON flows.cases;
CREATE POLICY cases_read ON flows.cases FOR SELECT TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR subject_contact_id = ANY (auth.contact_scope())
  );

DROP POLICY case_steps_read ON flows.case_steps;
CREATE POLICY case_steps_read ON flows.case_steps FOR SELECT TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
      SELECT 1 FROM flows.cases c
      WHERE c.id = flows.case_steps.case_id
        AND c.subject_contact_id = ANY (auth.contact_scope())
    )
  );

DROP POLICY executions_read ON flows.executions;
CREATE POLICY executions_read ON flows.executions FOR SELECT TO authenticated
  USING (tenant_id = ANY (auth.tenant_lineage()));
DROP FUNCTION IF EXISTS flows.execution_subject_contacts(int);

DROP POLICY domain_events_read ON flows.domain_events;
CREATE POLICY domain_events_read ON flows.domain_events FOR SELECT TO authenticated
  USING (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY shares_read ON documents.shares;
DROP POLICY shares_write ON documents.shares;
CREATE POLICY shares_read ON documents.shares FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY shares_write ON documents.shares FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY attachments_read ON documents.attachments;
DROP POLICY attachments_write ON documents.attachments;
CREATE POLICY attachments_read ON documents.attachments FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR contact_id = ANY (auth.contact_scope())
);
CREATE POLICY attachments_write ON documents.attachments FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY instructions_read ON platform.instructions;
DROP POLICY instructions_write ON platform.instructions;
CREATE POLICY instructions_read ON platform.instructions FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR account_id = ANY (auth.account_scope()));
CREATE POLICY instructions_write ON platform.instructions FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY tasks_write ON tasks.tasks;
CREATE POLICY tasks_write ON tasks.tasks FOR ALL TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR (
      tenant_id = ANY (auth.tenant_lineage())
      AND (assignee_contact_id IS NULL OR assignee_contact_id = ANY (auth.contact_scope()))
    )
    OR assignee_contact_id = ANY (auth.contact_scope())
  )
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    OR assignee_contact_id = ANY (auth.contact_scope())
  );

DROP POLICY applications_read ON crm.applications;
CREATE POLICY applications_read ON crm.applications FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR adviser_id = auth.uid()
);
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0031_book_scope_alignment.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
