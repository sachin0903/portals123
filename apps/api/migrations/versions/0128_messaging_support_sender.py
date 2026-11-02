"""record Graphene support message attribution

Revision ID: 0128_messaging_support_sender
Revises: 0127_permission_split
Create Date: 2026-09-10
"""

from pathlib import Path

from alembic import op

revision: str = "0128_messaging_support_sender"
down_revision: str | None = "0127_permission_split"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0128_messaging_support_sender.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.drop_column("messages", "sender_display_name", schema="messaging")
    op.drop_column("messages", "sender_is_support", schema="messaging")
