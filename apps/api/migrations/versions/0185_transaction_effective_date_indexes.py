"""Transactions: index the effective date without rewriting the table.

Revision ID: 0185_txn_effective_date_idx
Revises: 0184_terms
Create Date: 2026-09-24

The previous draft added a STORED generated column. PostgreSQL would rewrite every transaction
row while holding ACCESS EXCLUSIVE, which is not an online migration. The UTC-normalised
expression below is IMMUTABLE, so it can be indexed directly and built concurrently.
"""

from alembic import op

revision: str = "0185_txn_effective_date_idx"
down_revision: str | None = "0184_terms"
_ = (revision, down_revision)  # Alembic reads these names off the module

_ACCOUNT_INDEX = "platform.transactions_account_effective_date_idx"
_DATE_INDEX = "platform.transactions_effective_date_idx"
_EFFECTIVE_DATE = (
    "COALESCE(transaction_date, value_date, "
    "(posted_at AT TIME ZONE 'UTC')::date, "
    "(synced_at AT TIME ZONE 'UTC')::date)"
)


def upgrade() -> None:
    """Build the two indexes used by account and firm-wide transaction reads."""
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(
            "CREATE INDEX CONCURRENTLY transactions_account_effective_date_idx "
            f"ON platform.transactions (account_id, ({_EFFECTIVE_DATE}) DESC NULLS LAST, id DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY transactions_effective_date_idx "
            f"ON platform.transactions (({_EFFECTIVE_DATE}) DESC NULLS LAST, id DESC)"
        )
        op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_DATE_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_ACCOUNT_INDEX}")
        op.execute("SET lock_timeout = DEFAULT")
