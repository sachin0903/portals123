"""Join the monitoring-alert head to the FATCA question head

Revision ID: 0258_merge_alerted_and_fatca
Revises: 0254_compliance_check_alerted_at, 0257_fatca_status_question
Create Date: 2026-10-16

NO DDL. This revision exists only to give Alembic one head.

Main moved on to ``0257_fatca_status_question`` while this branch ended on
``0254_compliance_check_alerted_at``. Both were heads, so ``upgrade head`` could
not choose one.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would
restore two heads.

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.
"""

from collections.abc import Sequence

revision: str = "0258_merge_alerted_and_fatca"
down_revision: str | Sequence[str] | None = (
    "0254_compliance_check_alerted_at",
    "0257_fatca_status_question",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
