"""compliance: the stored back of an identity document

Revision ID: 0224_compliance_document_back
Revises: 0223_contact_rescreen
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0224_compliance_document_back"
down_revision: str | None = "0223_contact_rescreen"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0224_compliance_document_back.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE compliance.documents DROP COLUMN IF EXISTS back_object_key;
        """
    )
