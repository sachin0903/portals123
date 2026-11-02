"""Join the earlier SIPP-drawdown ids to each other

Revision ID: 0196_merge_sipp_drawdown
Revises: 0195_isa_inherit_gbp, 0186_isa_inherit_gbp
Create Date: 2026-09-25

NO DDL. The DDL now lives in 0197–0204, parented on ``0191_merge_transfers``.
This revision only keeps the two stub lineages that were already applied:

    0187_provider_columns -> 0188_outbound_transactions -> … -> 0195_isa_inherit_gbp
    0178_merge_transfer_facts -> 0179_outbound_transactions -> … -> 0186_isa_inherit_gbp

``0205_merge_sipp_drawdown`` joins this stub head to 0204. Do not re-point
0188's parent: that revision has already been applied.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would restore
two heads.
"""

from collections.abc import Sequence

revision: str = "0196_merge_sipp_drawdown"
down_revision: str | Sequence[str] | None = (
    "0195_isa_inherit_gbp",
    "0186_isa_inherit_gbp",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two stub lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
