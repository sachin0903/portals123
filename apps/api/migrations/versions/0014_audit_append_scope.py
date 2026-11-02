"""scope documents.audit_log INSERT policy to the caller's tenant

Revision ID: 0014_audit_append_scope
Revises: 0013_render_jobs
Create Date: 2026-07-15

Fixes the `documents_audit_append` policy from `WITH CHECK (true)` (arbitrary-tenant audit-row
forgery) to `tenant_id = auth.tenant_id()`. DDL in the sibling SQL file; applied as the elevated
migrator role. See the SQL file for why this keys on auth.tenant_id() and not tenant_lineage().
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_audit_append_scope"
down_revision: str | None = "0013_render_jobs"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0014_audit_append_scope.sql")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS documents_audit_append ON documents.audit_log")
    op.execute(
        "CREATE POLICY documents_audit_append ON documents.audit_log "
        "FOR INSERT TO authenticated WITH CHECK (true)"
    )
