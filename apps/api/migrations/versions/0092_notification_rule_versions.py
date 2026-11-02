"""notifications.rule_versions -- a full audit of every notification-rule change (who + what)

Revision ID: 0092_rule_versions
Revises: 0091_rule_email_code
Create Date: 2026-08-11

Snapshots a notification rule on every create/update/delete, with the tier that made the change
('operations' = Graphene, 'firm' = the firm) and the acting user — the notification-rule equivalent
of email.template_versions. DDL in the sibling SQL. (Revision id kept short — varchar(32).)
"""

from pathlib import Path

from alembic import op

revision: str = "0092_rule_versions"
down_revision: str | None = "0091_rule_email_code"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0092_notification_rule_versions.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS notifications.rule_versions")
