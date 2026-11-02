"""pipeline: keep a column out of the entity while the link still receives it

Revision ID: 0070_merge_linked_exclude
Revises: 0069_merge_pipeline_and_email
Create Date: 2026-08-06

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0070_merge_linked_exclude"
down_revision: str | None = "0069_merge_pipeline_and_email"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0070_merge_linked_exclude.sql")


def downgrade() -> None:
    # Leaves the 7-argument signature absent rather than restoring the 6-argument one: a body that
    # cannot express the exclusion is the state this migration exists to end, and every caller
    # passes the parameter's default anyway.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP FUNCTION IF EXISTS
          platform.merge_linked_entity(regclass, regclass, text, int, text, text[], text[]);
        RESET ROLE;
        """
    )
