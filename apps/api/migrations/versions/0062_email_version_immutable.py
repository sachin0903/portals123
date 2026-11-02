"""enforce published email template_version immutability (0042 had only a comment)

Revision ID: 0062_email_version_immutable
Revises: 0061_merge_email_authorship
Create Date: 2026-08-04

0042_email_templates.sql:37 documents "Immutable once status = 'published'" but nothing enforced it —
the same gap 0032 closed for documents.template_versions, in the schema cloned from it. Both roles
hold UPDATE on email.template_versions, and the published row is the one the SEND path resolves
through email.templates.current_published_version_id, so an in-place edit changes what clients
receive while skipping the version roll and the publish gates.

The trigger rejects a change to `schema`, `subject` or `preheader` — the three columns
app/email/render.py reads, i.e. the whole of what a client receives — on a row that is ALREADY
published. Status transitions and the publish-time stamps (published_at / published_by /
authored_by_scope, incl. an idempotent re-publish) still work. Reasoning in the sibling SQL file --
that file is the reviewable artifact; this script just applies it. Runs as the elevated migrator
role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
# NB: `alembic_version.version_num` is varchar(32) — the fuller name
# ("0062_email_published_version_immutable", 37 chars) does not fit and fails at stamp time.
revision: str = "0062_email_version_immutable"
down_revision: str | None = "0061_merge_email_authorship"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP TRIGGER IF EXISTS reject_published_edit ON email.template_versions;
DROP FUNCTION IF EXISTS email.reject_published_version_edit();
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0062_email_version_immutable.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
