"""auth.users: what a firm says about its staff, and which id each custodian knows them by

Revision ID: 0131_staff_profile
Revises: 0130_valuation_pension_split
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0131_staff_profile"
down_revision: str | None = "0130_valuation_pension_split"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0131_staff_profile.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE auth.users
          DROP CONSTRAINT IF EXISTS users_provider_refs_is_object,
          DROP COLUMN IF EXISTS job_title,
          DROP COLUMN IF EXISTS qualifications,
          DROP COLUMN IF EXISTS fca_reference,
          DROP COLUMN IF EXISTS bio,
          DROP COLUMN IF EXISTS photo_url,
          DROP COLUMN IF EXISTS direct_phone,
          DROP COLUMN IF EXISTS provider_refs;
        """
    )
