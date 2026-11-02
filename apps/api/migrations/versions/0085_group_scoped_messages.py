"""tag a message with the group it was sent through

Revision ID: 0085_group_scoped_messages
Revises: 0084_messaging_groups
Create Date: 2026-08-07

Without this, a group's "merged feed" was really just every message across its members' whole
1:1 histories with their adviser — including messages that had nothing to do with the group.
`group_id` scopes a group's own feed to messages actually sent THROUGH it, and lets the adviser's
Direct-message views filter those broadcasts back out, so a group send never shows up looking
like an ordinary 1:1 reply on the staff side.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0085_group_scoped_messages"
down_revision: str | None = "0084_messaging_groups"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0085_group_scoped_messages.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "ALTER TABLE messaging.messages DROP COLUMN IF EXISTS group_id;"
    )
