"""platform.providers folds into tenant_platforms: one row per firm's connection

Revision ID: 0145_one_provider_table
Revises: 0144_merge_enum_provider
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0145_one_provider_table"
down_revision: str | None = "0144_merge_enum_provider"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0145_one_provider_table.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The SHAPE comes back and the CONTENT cannot: the merged config is one object now, and
    # splitting it would mean deciding which keys were the connection's and which the firm's —
    # a guess about data an operator entered. Roll forward.
    raise NotImplementedError(
        "0133 merges platform.providers.config into platform.tenant_platforms.config. The two "
        "are one object afterwards and cannot be told apart. Roll forward instead."
    )
