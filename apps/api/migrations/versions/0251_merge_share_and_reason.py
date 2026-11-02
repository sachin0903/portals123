"""Join illustration sharing to the justification head

Revision ID: 0251_merge_share_and_reason
Revises: 0248_illustration_sharing, 0250_require_justification
Create Date: 2026-10-12

NO DDL. This revision exists only to give Alembic one head.

Main took ``0248_illustration_sharing`` while this branch ended on
``0250_require_justification``. Both were heads, so ``upgrade head`` could
not choose one.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would
restore two heads.
"""

from collections.abc import Sequence

revision: str = "0251_merge_share_and_reason"
down_revision: str | Sequence[str] | None = (
    "0248_illustration_sharing",
    "0250_require_justification",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
