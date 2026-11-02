"""email.* — the email-template authoring store

Revision ID: 0042_email_templates
Revises: 0050_flow_ai
Create Date: 2026-08-03

A dedicated `email` schema (templates + template_versions) mirroring the 0012 documents template
store, with email-specific subject/preheader on the version and its own email_templates:* perms so
email editing gates independently of documents. DDL in the sibling SQL file; applied as the elevated
migrator role.

Re-parented onto 0050_flow_ai when this feature branch rebased onto main's flows work (the email
schema is independent of the flows tables, so it simply chains after them — the numeric prefix stays
0042 per the repo's merge convention; alembic tracks the revision id, not the filename number).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_email_templates"
down_revision: str | None = "0050_flow_ai"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # encoding="utf-8" explicitly: the SQL files are UTF-8, but Windows' default read_text() codec is
    # cp1252, which chokes on non-ASCII bytes (a real cross-migration Windows-dev trap).
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0042_email_templates.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS email CASCADE")
