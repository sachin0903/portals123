"""Bind SIPP onboarding to a prior illustration

Revision ID: 0173_sipp_illustration
Revises: 0172_instruction_fee_type
"""

from pathlib import Path

from alembic import op

revision: str = "0173_sipp_illustration"
down_revision: str | None = "0172_instruction_fee_type"
_ = (revision, down_revision)  # Alembic reads these names off the module
_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0173_sipp_onboarding_illustration.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS platform.accounts_onboarding_illustration_idx;
        ALTER TABLE platform.accounts
          DROP CONSTRAINT IF EXISTS accounts_onboarding_illustration_tenant_fkey,
          DROP COLUMN IF EXISTS onboarding_illustration_id;
        ALTER TABLE platform.illustrations
          DROP CONSTRAINT IF EXISTS illustrations_tenant_id_key;
        """
    )
