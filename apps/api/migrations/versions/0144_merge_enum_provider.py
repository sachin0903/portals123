"""the merge functions compare provider against the enum, not text

Revision ID: 0144_merge_enum_provider
Revises: 0143_one_provider_vocabulary
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0144_merge_enum_provider"
down_revision: str | None = "0143_one_provider_vocabulary"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0144_merge_enum_provider.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Nothing to undo separately: these bodies only make sense against 0131's enum, and 0131
    # itself is forward-only.
    pass
