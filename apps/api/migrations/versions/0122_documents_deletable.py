"""a document can be marked as one the firm may not delete

Revision ID: 0122_documents_deletable
Revises: 0121_user_types
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0122_documents_deletable"
down_revision: str | None = "0121_user_types"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0122_documents_deletable.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE documents.documents DROP COLUMN IF EXISTS deletable;"
    )
