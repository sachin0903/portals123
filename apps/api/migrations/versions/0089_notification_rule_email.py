"""notifications.rules -- per-rule email subject/body (fold email into the builder)

Revision ID: 0089_notification_rule_email
Revises: 0088_preference_trigger_key
Create Date: 2026-08-10

A notification's email is authored in the same builder as its in-app copy. `email_subject` /
`email_body` hold the email's own wording; NULL falls back to the in-app title/body, so existing
rules are unchanged. Expand phase; DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0089_notification_rule_email"
down_revision: str | None = "0088_preference_trigger_key"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0089_notification_rule_email.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.rules "
        "DROP COLUMN IF EXISTS email_subject, DROP COLUMN IF EXISTS email_body"
    )
