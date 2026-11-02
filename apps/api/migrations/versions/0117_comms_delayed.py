"""a delayed delivery is its own status, not a silent one

Revision ID: 0117_comms_delayed
Revises: 0116_comms_messages
Create Date: 2026-09-01
"""

from pathlib import Path

from alembic import op

revision: str = "0117_comms_delayed"
down_revision: str | None = "0116_comms_messages"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0117_comms_delayed.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Rows already recorded as `delayed` would violate the narrower CHECK, so they are settled back
    # to `sent` — the status 0111 gave them — rather than blocking the downgrade.
    op.get_bind().exec_driver_sql(
        "UPDATE comms.messages SET status = 'sent' WHERE status = 'delayed';"
        "ALTER TABLE comms.messages DROP COLUMN IF EXISTS delayed_at;"
        "ALTER TABLE comms.messages DROP CONSTRAINT IF EXISTS messages_status_check;"
        # No 'opened'/'clicked' here either: 0111 no longer creates their stamp columns, so a row
        # restored to one of those statuses would have nowhere to record when it happened. The
        # downgrade inlines its own SQL rather than reading a file, which is exactly how it kept
        # the old vocabulary after 0111/0112's .sql was narrowed.
        "ALTER TABLE comms.messages ADD CONSTRAINT messages_status_check CHECK (status IN ("
        "  'unconfigured', 'suppressed', 'failed', 'sent', 'delivered',"
        "  'bounced', 'complained'));"
    )
