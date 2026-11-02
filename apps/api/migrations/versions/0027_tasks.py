"""standalone tasks: first-class action items + the role_ids claim

Revision ID: 0027_tasks
Revises: 0022_documents_activity_scope
Create Date: 2026-07-24

The tasks schema (tasks.tasks): tenant-scoped action items with a polymorphic assignee
(staff user / staff role / client contact), optional polymorphic subject, provenance +
source_ref idempotency, RLS (staff lineage / client contact-scope), audit + FORCE RLS.
Also extends auth.resolve_claims to mint 'role_ids' and adds the auth.role_ids() reader.
Design record: docs/tasks.md. DDL in the sibling SQL file; applied as the migrator role.

Chains from 0022_documents_activity_scope (the head) — the filename sequence and the
revision chain diverged after 0026; the chain is authoritative.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_tasks"
down_revision: str | None = "0022_documents_activity_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Drops the schema + the claims reader. auth.resolve_claims is left minting the (now unread)
# 'role_ids' key — restoring the pre-0027 body would drop it for no behavioural difference,
# since an extra claim key nothing reads is inert.
_DOWN = """
DROP SCHEMA IF EXISTS tasks CASCADE;
DROP FUNCTION IF EXISTS auth.role_ids();
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0027_tasks.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
