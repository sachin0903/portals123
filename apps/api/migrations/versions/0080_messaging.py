"""messaging — a prototype client<->adviser chat (messaging.conversations/messages)

Revision ID: 0080_messaging
Revises: 0079_last_merged_at
Create Date: 2026-08-04

One conversation per client, keyed to whichever adviser crm.client_advisers currently names for
that contact (a mutable pointer, re-synced on read — no reassignment ledger yet). Messages are
append-only. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0080_messaging"
down_revision: str | None = "0079_last_merged_at"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0080_messaging.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP TABLE IF EXISTS messaging.messages;"
        "DROP TABLE IF EXISTS messaging.conversations;"
        "DROP SCHEMA IF EXISTS messaging;"
    )
