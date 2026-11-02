"""email.template_versions.authored_by_scope — say whether the firm or Graphene authored a version

Revision ID: 0051_email_version_authorship
Revises: 0042_email_templates
Create Date: 2026-08-04

The firm router and the admin door write email-template versions through ONE service, and the read
projection resolves `created_by` to a person's name — so an operator editing a firm's template on
their behalf was indistinguishable from the firm's own staff doing it. This adds the authoring SIDE
as a column stamped at write time (never derived from the author's tenant membership, which is
mutable). Nullable with no default: the pre-existing rows' authorship is genuinely unknown, and
defaulting them to 'firm' would back-date a false claim about exactly the rows the feature exists
to expose. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0051_email_version_authorship"
down_revision: str | None = "0042_email_templates"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # encoding="utf-8" explicitly: the SQL files are UTF-8, but Windows' default read_text() codec
    # is cp1252, which chokes on non-ASCII bytes (a real cross-migration Windows-dev trap).
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0051_email_version_authorship.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "ALTER TABLE email.template_versions "
        "  DROP CONSTRAINT IF EXISTS email_template_versions_authored_by_scope_chk;"
        "ALTER TABLE email.template_versions DROP COLUMN IF EXISTS authored_by_scope;"
    )
