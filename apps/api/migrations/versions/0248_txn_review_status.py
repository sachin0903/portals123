"""Four-eye stage on a Graphene money row

Revision ID: 0248_txn_review_status
Revises: 0247_adviser_provider_orgs
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision: str = "0248_txn_review_status"
down_revision: str | None = "0247_adviser_provider_orgs"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0248_txn_review_status.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE platform.transactions
          DROP CONSTRAINT IF EXISTS transactions_review_status,
          DROP COLUMN IF EXISTS review_status;
        """
    )
