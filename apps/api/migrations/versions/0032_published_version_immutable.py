"""enforce published template_version immutability with a trigger (0012 had only a comment)

Revision ID: 0032_published_version_immutable
Revises: 0031_book_scope_alignment
Create Date: 2026-07-28

0012_templates.sql:43 documents "immutable once status = 'published'" but nothing enforced it, and
the gap is reachable: PATCH /templates/{uid} applies a renderer change to max(version_number)
regardless of status, so a live published version could be mutated underneath every document
generated from it. The trigger rejects a change to `schema` (the content) or `renderer` on a row
that is already published; status transitions and the publish-time derivations (input_schema,
fixtures, input_form_snapshot — written after the status flip) still work.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_published_version_immutable"
down_revision: str | None = "0031_book_scope_alignment"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP TRIGGER IF EXISTS reject_published_edit ON documents.template_versions;
DROP FUNCTION IF EXISTS documents.reject_published_version_edit();
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0032_published_version_immutable.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
