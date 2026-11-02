"""Pending outbound transactions for instructed money movement

Revision ID: 0197_outbound_transactions
Revises: 0191_merge_transfers
Create Date: 2026-09-21

First applied as ``0179_outbound_transactions``, then as
``0188_outbound_transactions`` once main had taken 0179–0187. Main then took
0188–0191, so the DDL lives here, parented on current main. Both earlier ids
remain no-op stubs.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0197_outbound_transactions"
down_revision: str | Sequence[str] | None = "0191_merge_transfers"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0197_outbound_transactions.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS platform.transactions_outbound_origin_idx;
        ALTER TABLE platform.transactions
          DROP CONSTRAINT IF EXISTS transactions_outbound_status;
        -- provider_code.graphene is not dropped: enum members are never removed.
        """
    )
