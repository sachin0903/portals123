"""pipeline: mint a model version only when the allocation changed

Revision ID: 0068_merge_model_versions
Revises: 0067_merge_entity_options
Create Date: 2026-08-06

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0068_merge_model_versions"
down_revision: str | None = "0067_merge_entity_options"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0068_merge_model_versions.sql")


def downgrade() -> None:
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP FUNCTION IF EXISTS platform.merge_model_commentary(regclass, int, text);
        DROP FUNCTION IF EXISTS platform.merge_model_versions(regclass, int, text);
        RESET ROLE;
        """
    )
