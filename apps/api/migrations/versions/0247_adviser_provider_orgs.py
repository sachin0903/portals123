"""The custodian organisation an adviser opens clients under

Revision ID: 0247_adviser_provider_orgs
Revises: 0246_merge_fee_and_party
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision: str = "0247_adviser_provider_orgs"
down_revision: str | None = "0246_merge_fee_and_party"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0247_adviser_provider_orgs.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE auth.users
          DROP CONSTRAINT IF EXISTS users_provider_orgs_object,
          DROP COLUMN IF EXISTS provider_orgs;
        """
    )
