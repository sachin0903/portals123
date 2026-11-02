"""messaging request items — link a chat message to the document-request item it announces

Revision ID: 0083_messaging_request_items
Revises: 0082_storage_tenants_client_read
Create Date: 2026-08-05

Nullable messaging.messages.request_item_id -> documents.request_items(id), so the client's
thread can render a rich "upload" card for a pending request instead of plain text. DDL in the
sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0083_messaging_request_items"
down_revision: str | None = "0082_storage_tenants_client_read"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0083_messaging_request_items.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "ALTER TABLE messaging.messages DROP COLUMN IF EXISTS request_item_id;"
    )
