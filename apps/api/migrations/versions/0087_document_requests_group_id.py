"""tag a request with the group it was raised through

Revision ID: 0087_document_requests_group_id
Revises: 0086_group_batches
Create Date: 2026-08-08

Without this, a request raised via a group had no durable link back to it — so a client's later
fulfilment of it had no way to know it should surface in the group's own feed too, only in the
member's 1:1 thread. `group_id` on `documents.requests` is that link.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0087_document_requests_group_id"
down_revision: str | None = "0086_group_batches"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0087_document_requests_group_id.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "ALTER TABLE documents.requests DROP COLUMN IF EXISTS group_id;"
    )
