"""client classifications: book-level portal assignment

Revision ID: 0181_client_classifications
Revises: 0180_review_case_no
Create Date: 2026-09-23
"""

from pathlib import Path

from alembic import op

revision: str = "0181_client_classifications"
down_revision: str | None = "0180_review_case_no"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0181_client_classifications.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE crm.contacts DROP CONSTRAINT IF EXISTS contacts_classification_tenant_fkey;
        ALTER TABLE crm.contacts DROP COLUMN IF EXISTS classification_id;
        DROP TABLE IF EXISTS crm.client_classifications;
        """
    )
