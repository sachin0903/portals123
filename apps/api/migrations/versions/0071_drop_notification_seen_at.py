"""drop notifications.notifications.seen_at -- the dead 'badge acknowledged' state

Revision ID: 0071_drop_notification_seen_at
Revises: 0070_tenant_config_audience
Create Date: 2026-08-07

The badge is an UNREAD count that clears only on read/dismiss/mark-all, so the 'seen' state (and its
mark-seen endpoint) has no consumer. Contract phase; DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0071_drop_notification_seen_at"
down_revision: str | None = "0070_tenant_config_audience"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0071_drop_notification_seen_at.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.notifications ADD COLUMN IF NOT EXISTS seen_at timestamptz"
    )
