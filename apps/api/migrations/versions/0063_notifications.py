"""notifications -- the in-app notification centre (schema + RLS + consumer cursor)

Revision ID: 0063_notifications
Revises: 0062_email_version_immutable
Create Date: 2026-08-06

Event-driven in-app notifications: a per-recipient feed (notifications.notifications), per-category
channel overrides (notifications.preferences), and the durable consumer position over audit.actions
(notifications.cursor, a copy of flows.automation_cursor). Writer is service_role (the tail consumer
and the domain notify() primitive); a signed-in user may only mark their own rows read/seen. Expand
only. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0063_notifications"
down_revision: str | None = "0062_email_version_immutable"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0063_notifications.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS notifications CASCADE")
