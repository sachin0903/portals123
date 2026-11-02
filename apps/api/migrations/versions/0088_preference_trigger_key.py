"""notifications.preferences -- per-notification grain (trigger_key) for client prefs

Revision ID: 0088_preference_trigger_key
Revises: 0087_document_requests_group_id
Create Date: 2026-08-07

Clients set preferences per notification (per trigger), not per category. `trigger_key` NULL = a
category-level preference (staff); set = a per-notification override (clients). Expand phase; DDL in
the sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0088_preference_trigger_key"
down_revision: str | None = "0087_document_requests_group_id"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0088_preference_trigger_key.sql")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE notifications.preferences DROP CONSTRAINT IF EXISTS preferences_key"
    )
    # Collapse per-trigger rows to the category grain before restoring the old key.
    bind.exec_driver_sql(
        "DELETE FROM notifications.preferences a USING notifications.preferences b "
        "WHERE a.ctid < b.ctid AND a.recipient_kind = b.recipient_kind "
        "  AND a.recipient_user_id IS NOT DISTINCT FROM b.recipient_user_id "
        "  AND a.recipient_contact_id IS NOT DISTINCT FROM b.recipient_contact_id "
        "  AND a.category = b.category"
    )
    bind.exec_driver_sql(
        "ALTER TABLE notifications.preferences DROP COLUMN IF EXISTS trigger_key, "
        "ADD CONSTRAINT preferences_key UNIQUE NULLS NOT DISTINCT "
        "  (recipient_kind, recipient_user_id, recipient_contact_id, category)"
    )
