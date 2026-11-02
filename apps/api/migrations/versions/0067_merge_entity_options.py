"""pipeline: merge_entity gains a partial-index predicate and an insert-only mode

Revision ID: 0067_merge_entity_options
Revises: 0066_links_tenant_key
Create Date: 2026-08-06

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0067_merge_entity_options"
down_revision: str | None = "0066_links_tenant_key"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0067_merge_entity_options.sql")


def downgrade() -> None:
    # Leaves the 4-argument signature absent: restoring it would mean re-creating a body that
    # cannot express the catalogue's two cases, and every caller passes the default anyway.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP FUNCTION IF EXISTS
          platform.merge_entity(regclass, regclass, text[], text[], text);
        RESET ROLE;
        """
    )
