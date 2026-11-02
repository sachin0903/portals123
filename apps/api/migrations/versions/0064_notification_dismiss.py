"""notification dismiss -- a recipient can remove a notification from their feed (soft)

Revision ID: 0064_notification_dismiss
Revises: 0063_notifications
Create Date: 2026-08-06

Adds notifications.notifications.dismissed_at. Soft rather than a delete so a consumer replay cannot
resurrect a dismissed notification (the idempotency key survives). Expand only; the column is
nullable with no default, so existing rows read as not-dismissed. DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0064_notification_dismiss"
down_revision: str | None = "0063_notifications"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0064_notification_dismiss.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.notifications DROP COLUMN IF EXISTS dismissed_at"
    )
