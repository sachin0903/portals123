"""document render queue (documents.render_jobs)

Revision ID: 0013_render_jobs
Revises: 0012_templates
Create Date: 2026-07-14

The durable queue the DocumentWorker drains (SELECT ... FOR UPDATE SKIP LOCKED) to render generated
documents asynchronously (docs/document-generation.md §8/§10). DDL in the sibling SQL file; applied
as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_render_jobs"
down_revision: str | None = "0012_templates"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0013_render_jobs.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.render_jobs CASCADE")
