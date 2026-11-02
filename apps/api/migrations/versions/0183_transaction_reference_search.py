"""Search transactions by reference only.

Revision ID: 0183_txn_reference_search
Revises: 0182_spotlight_search
Create Date: 2026-09-23
"""

from alembic import op

revision: str = "0183_txn_reference_search"
down_revision: str | None = "0182_spotlight_search"
_ = (revision, down_revision)  # Alembic reads these names off the module

_TRIGRAM_INDEX = "platform.transactions_spotlight_trgm"
_REFERENCE_INDEX = "platform.transactions_reference_search_idx"


def upgrade() -> None:
    """Replace the wide fuzzy-search index with the one lookup Spotlight still performs.

    ``transactions_source_unique`` starts with ``source_system`` and therefore cannot efficiently
    answer a reference-only predicate. The expression index matches the query exactly and
    ``text_pattern_ops`` covers both equality and the intentional prefix lookup.
    """
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_REFERENCE_INDEX}")
        op.execute(
            "CREATE INDEX CONCURRENTLY transactions_reference_search_idx "
            "ON platform.transactions (lower(source_transaction_id) text_pattern_ops)"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_TRIGRAM_INDEX}")
        op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_REFERENCE_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_TRIGRAM_INDEX}")
        op.execute(
            "CREATE INDEX CONCURRENTLY transactions_spotlight_trgm "
            "ON platform.transactions USING gin ("
            "search.normalise("
            "source_transaction_id, source_type, source_subtype, "
            "isin, asset_name, currency, status"
            ") gin_trgm_ops)"
        )
        op.execute("SET lock_timeout = DEFAULT")
