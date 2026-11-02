"""notifications.rules -- per-rule rich email design (html + css from the shared editor)

Revision ID: 0090_rule_email_design
Revises: 0089_notification_rule_email
Create Date: 2026-08-10

The notification email is authored per rule in the shared email editor; `email_html`/`email_css`
hold its output. `email_subject`/`email_body` (0081) stay as the subject + plain fallback. (Revision
id kept short — `alembic_version.version_num` is varchar(32).) Expand phase; DDL in the sibling SQL.
"""

from pathlib import Path

from alembic import op

revision: str = "0090_rule_email_design"
down_revision: str | None = "0089_notification_rule_email"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0090_rule_email_design.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.rules "
        "DROP COLUMN IF EXISTS email_html, DROP COLUMN IF EXISTS email_css"
    )
