"""models: drop platform_identifier, superseded by platform.model_platforms

Revision ID: 0063_models_drop_identifier
Revises: 0062_pipeline_inbound
Create Date: 2026-08-05

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0063_models_drop_identifier"
down_revision: str | None = "0062_pipeline_inbound"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0063_models_drop_identifier.sql")


def downgrade() -> None:
    # The column comes back empty, which is the honest round trip: its values moved to
    # platform.model_platforms.external_id long before this migration, and nothing has written
    # this column since. Re-deriving it would invent a fact.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        ALTER TABLE platform.models ADD COLUMN IF NOT EXISTS platform_identifier text;
        RESET ROLE;
        """
    )
