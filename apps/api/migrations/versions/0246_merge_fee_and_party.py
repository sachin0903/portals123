"""Join the adviser-fee revision to the account-party head

Revision ID: 0246_merge_fee_and_party
Revises: 0245_instruction_adviser_fee, 0244_merge_account_party
Create Date: 2026-10-10

NO DDL. This revision exists only to give Alembic one head.

Main took ``0244_merge_account_party`` while this branch ended on
``0245_instruction_adviser_fee``. Both were heads, so ``upgrade head`` could
not choose one.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would
restore two heads.
"""

from collections.abc import Sequence

revision: str = "0246_merge_fee_and_party"
down_revision: str | Sequence[str] | None = (
    "0245_instruction_adviser_fee",
    "0244_merge_account_party",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
