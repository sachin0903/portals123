"""template + dataset authoring tables (generation's authoring half)

Revision ID: 0012_templates
Revises: 0011_forms
Create Date: 2026-07-13

Lands documents.templates / template_versions / datasets / dataset_versions (+ enums, RLS, the
deferred templates↔versions FK, and the documents.documents.template_version_id FK). Authoring only —
the async render pipeline (PGMQ worker, WeasyPrint, POST /documents generate) is a separate later
slice. DDL in the sibling SQL file; applied as the elevated migrator role. See docs/document-generation.md.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_templates"
down_revision: str | None = "0011_forms"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0012_templates.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE documents.documents DROP CONSTRAINT IF EXISTS fk_template_version")
    op.execute("DROP TABLE IF EXISTS documents.dataset_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS documents.datasets CASCADE")
    op.execute("DROP TABLE IF EXISTS documents.template_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS documents.templates CASCADE")
    for enum in ("dataset_scope", "dataset_source", "renderer", "template_status"):
        op.execute(f"DROP TYPE IF EXISTS documents.{enum}")
