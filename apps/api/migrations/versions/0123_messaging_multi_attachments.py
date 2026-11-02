"""a message may carry several attachments

Revision ID: 0123_messaging_multi_attachments
Revises: 0122_documents_deletable
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0123_messaging_multi_attachments"
down_revision: str | None = "0122_documents_deletable"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0123_messaging_multi_attachments.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Only reversible while no message holds more than one attachment; the constraint refuses
    # otherwise, which is the honest outcome rather than silently discarding files.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE messaging.attachments "
        "ADD CONSTRAINT attachments_message_id_key UNIQUE (message_id);"
    )
