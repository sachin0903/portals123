"""Per-product account-open reference (SEI enrolment template / Seccl schemeProductId)

Revision ID: 0199_account_open_reference
Revises: 0198_contacts_sync_morgan_lloyd
Create Date: 2026-09-22

First applied as ``0181_account_open_reference``, then as ``0190_account_open_reference``.
"""

from pathlib import Path

from alembic import op

revision: str = "0199_account_open_reference"
down_revision: str | None = "0198_contacts_sync_morgan_lloyd"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0199_account_open_reference.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.product_platforms
          DROP COLUMN IF EXISTS account_open_reference;
        """
    )
