"""compliance: identity document sides and the firm webhook delivery time

Revision ID: 0222_partner_api_extras
Revises: 0221_complycube_core
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0222_partner_api_extras"
down_revision: str | None = "0221_complycube_core"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0222_partner_api_extras.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE compliance.webhook_deliveries DROP COLUMN IF EXISTS sent_at;
        ALTER TABLE compliance.documents
          DROP CONSTRAINT IF EXISTS compliance_documents_side_check,
          DROP COLUMN IF EXISTS side;
        """
    )
