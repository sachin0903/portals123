"""Keep the 0188 outbound-transactions revision id locatable

Revision ID: 0188_outbound_transactions
Revises: 0187_provider_columns
Create Date: 2026-09-21

NO DDL. The pending-transaction columns live in `0197_outbound_transactions`.

This id carried the DDL after the first renumber (0179 → 0188), so a database
stamped with it can still `upgrade head`. Main then took 0188–0191. Do not
re-point this parent onto 0179 or onto `0191_merge_transfers`: this revision
has already been applied on the linear path.
"""

from collections.abc import Sequence

revision: str = "0188_outbound_transactions"
down_revision: str | Sequence[str] | None = "0187_provider_columns"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0198 carries the columns."""


def downgrade() -> None:
    """Nothing to undo. The objects belong to 0198."""
