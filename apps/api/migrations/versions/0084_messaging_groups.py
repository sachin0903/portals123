"""messaging groups — a staff-only saved shortcut to a set of clients

Revision ID: 0084_messaging_groups
Revises: 0083_messaging_request_items
Create Date: 2026-08-06

messaging.groups + messaging.group_members: NOT a new conversation type — every client still has
exactly one real conversation; sending "to a group" fans out into each member's own conversation.
RLS is staff-only (tenant_lineage(), no contact_scope() branch) — a client must never see that
grouping exists at all. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0084_messaging_groups"
down_revision: str | None = "0083_messaging_request_items"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0084_messaging_groups.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP TABLE IF EXISTS messaging.group_members;"
        "DROP TABLE IF EXISTS messaging.groups;"
    )
