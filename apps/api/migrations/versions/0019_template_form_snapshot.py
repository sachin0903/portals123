"""snapshot the linked input form into a published template version

Revision ID: 0019_template_form_snapshot
Revises: 0018_attachments
Create Date: 2026-07-16

A published template version freezes its linked input form (template_versions.input_form_snapshot),
so editing the form doesn't change already-published templates — you republish to pick up changes.
DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_template_form_snapshot"
down_revision: str | None = "0018_attachments"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0019_template_form_snapshot.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE documents.template_versions DROP COLUMN IF EXISTS input_form_snapshot")
