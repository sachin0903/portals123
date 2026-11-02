"""the outbound-email ledger stores the wire request, not only the preview body

Revision ID: 0118_comms_request
Revises: 0117_comms_delayed
Create Date: 2026-09-03
"""

from pathlib import Path

from alembic import op

revision: str = "0118_comms_request"
down_revision: str | None = "0117_comms_delayed"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0118_comms_request.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE comms.messages DROP COLUMN IF EXISTS request;")
