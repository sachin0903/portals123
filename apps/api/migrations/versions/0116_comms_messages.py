"""the outbound-email ledger: one row per send, plus the provider's delivery callbacks

Revision ID: 0116_comms_messages
Revises: 0115_catalogue_uids
Create Date: 2026-09-01
"""

from pathlib import Path

from alembic import op

revision: str = "0116_comms_messages"
down_revision: str | None = "0115_catalogue_uids"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0116_comms_messages.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The whole schema is this migration's, so dropping it is the exact inverse. CASCADE takes the
    # two tables, their indexes and their policies together.
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS comms CASCADE;")
