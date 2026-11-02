"""Join the AML rescreen head to the statement-layer head

Revision ID: 0254_merge_aml_and_reason
Revises: 0251_merge_share_and_reason, 0253_aml_rescreen_on_change
Create Date: 2026-10-14

NO DDL. This revision exists only to give Alembic one head.

Main moved on to ``0253_aml_rescreen_on_change`` while this branch ended on
``0251_merge_share_and_reason``. Both were heads, so ``upgrade head`` could
not choose one.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would
restore two heads.

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.
"""

from collections.abc import Sequence

revision: str = "0254_merge_aml_and_reason"
down_revision: str | Sequence[str] | None = (
    "0251_merge_share_and_reason",
    "0253_aml_rescreen_on_change",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
