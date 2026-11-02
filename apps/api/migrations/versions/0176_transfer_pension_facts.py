"""Crystallisation, ceding pension type and safeguarded rights on transfers

Revision ID: 0176_transfer_pension_facts
Revises: 0175_notification_portal_scope
Create Date: 2026-09-16
"""

from pathlib import Path

from alembic import op

revision: str = "0176_transfer_pension_facts"
down_revision: str | None = "0175_notification_portal_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0176_transfer_pension_facts.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.transfers
          DROP CONSTRAINT IF EXISTS transfers_crystallisation_check,
          DROP CONSTRAINT IF EXISTS transfers_pension_type_check,
          DROP COLUMN IF EXISTS crystallisation,
          DROP COLUMN IF EXISTS pension_type,
          DROP COLUMN IF EXISTS safeguarded_benefits;
        """
    )
