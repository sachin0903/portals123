"""tasks phase 4: notification stamps + book-scoped staff visibility

Revision ID: 0028_tasks_polish
Revises: 0027_tasks
Create Date: 2026-07-25

Adds the notifier's idempotency stamps (notified_at / due_soon_notified_at + scan indexes) and
re-scopes the staff RLS branch to the adviser's book, mirroring 0021_documents_scope: full-book
staff (contacts:read_all) see everything; book-scoped staff see staff/role work and their own
book's client tasks; clients are unchanged. DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_tasks_polish"
down_revision: str | None = "0027_tasks"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the 0027 policies (tenant-wide staff branch) and drop the notifier columns.
_DOWN = """
ALTER TABLE tasks.tasks DROP COLUMN notified_at, DROP COLUMN due_soon_notified_at;

DROP POLICY tasks_read ON tasks.tasks;
DROP POLICY tasks_write ON tasks.tasks;
CREATE POLICY tasks_read ON tasks.tasks FOR SELECT TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR assignee_contact_id = ANY (auth.contact_scope())
  );
CREATE POLICY tasks_write ON tasks.tasks FOR ALL TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR assignee_contact_id = ANY (auth.contact_scope())
  )
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    OR assignee_contact_id = ANY (auth.contact_scope())
  );
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0028_tasks_polish.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
