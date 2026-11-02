"""notifications.rules -- select an email TEMPLATE by code (supersedes the per-rule email design)

Revision ID: 0091_rule_email_code
Revises: 0090_rule_email_design
Create Date: 2026-08-11

A notification rule points at a shared email template by `code` (email.templates) instead of
carrying its own subject/body/html/css. Drops the four 0081/0082 columns (never read by a send
path) and adds `email_code`. NULL = the general `notification` wrapper. Expand phase; DDL in the
sibling SQL. (Revision id kept short — `alembic_version.version_num` is varchar(32).)
"""

from pathlib import Path

from alembic import op

revision: str = "0091_rule_email_code"
down_revision: str | None = "0090_rule_email_design"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0091_rule_email_code.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.rules "
        "DROP COLUMN IF EXISTS email_code, "
        "ADD COLUMN email_subject text, ADD COLUMN email_body text, "
        "ADD COLUMN email_html text, ADD COLUMN email_css text"
    )
