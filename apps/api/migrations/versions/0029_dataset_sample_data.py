"""preview-only sample_data on dataset_versions

Revision ID: 0029_dataset_sample_data
Revises: 0028_tasks_polish
Create Date: 2026-07-22

Adds a nullable ``sample_data jsonb`` to documents.dataset_versions — author-supplied preview rows
for the template editor's live preview, NEVER resolved at generate. DDL in the sibling SQL file;
applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_dataset_sample_data"
down_revision: str | None = "0028_tasks_polish"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0029_dataset_sample_data.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE documents.dataset_versions DROP COLUMN sample_data;")
