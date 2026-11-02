"""messaging attachments — one file per message, stored in the tenant's own bucket

Revision ID: 0081_messaging_attachments
Revises: 0080_messaging
Create Date: 2026-08-05

messaging.messages.body becomes nullable (a message may be attachment-only); the new
messaging.attachments table is append-only, RLS-matched to messages. DDL in the sibling SQL file;
applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0081_messaging_attachments"
down_revision: str | None = "0080_messaging"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0081_messaging_attachments.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP TABLE IF EXISTS messaging.attachments;"
        "ALTER TABLE messaging.messages DROP CONSTRAINT IF EXISTS messages_body_check;"
        "ALTER TABLE messaging.messages ADD CONSTRAINT messages_body_check "
        "  CHECK (btrim(body) <> '');"
        "UPDATE messaging.messages SET body = '' WHERE body IS NULL;"
        "ALTER TABLE messaging.messages ALTER COLUMN body SET NOT NULL;"
    )
