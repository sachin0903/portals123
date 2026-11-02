"""pipeline: platform.merge_linked_entity — identity resolved through platform.links

Revision ID: 0065_merge_linked_entity
Revises: 0064_merge_entity
Create Date: 2026-08-06

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0065_merge_linked_entity"
down_revision: str | None = "0064_merge_entity"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0065_merge_linked_entity.sql")


def downgrade() -> None:
    # merge_entity is left as this migration replaced it: the previous body differs only in
    # casting, and restoring it would leave a function that cannot read a real landing table.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP INDEX IF EXISTS platform.links_resolve_idx;
        DROP FUNCTION IF EXISTS
          platform.merge_linked_entity(regclass, regclass, text, int, text, text[]);
        DROP FUNCTION IF EXISTS platform._merge_cast_list(regclass, text[], text);
        RESET ROLE;
        """
    )
