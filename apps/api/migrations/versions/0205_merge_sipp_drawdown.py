"""Join the SIPP-drawdown DDL onto the stub lineages

Revision ID: 0205_merge_sipp_drawdown
Revises: 0204_isa_inherit_gbp, 0196_merge_sipp_drawdown
Create Date: 2026-09-29

NO DDL. Main's head is ``0191_merge_transfers``. The feature DDL continues
from there as 0197–0204. Databases already stamped with 0179–0186 or with
0188–0196 still resolve those ids (no-op stubs, joined at
``0196_merge_sipp_drawdown``). This revision requires both lineages, so
`upgrade head` has one destination.

``alembic_version.version_num`` is ``varchar(32)``; this id is 24 characters.
"""

from collections.abc import Sequence

revision: str = "0205_merge_sipp_drawdown"
down_revision: str | Sequence[str] | None = (
    "0204_isa_inherit_gbp",
    "0196_merge_sipp_drawdown",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
