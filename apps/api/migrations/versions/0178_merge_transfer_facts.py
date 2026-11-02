"""Join the renamed transfer-facts id to the linear 0173–0178_seed_book_job line

Revision ID: 0178_merge_transfer_facts
Revises: 0178_seed_book_job, 0173_transfer_pension_facts
Create Date: 2026-09-18

NO DDL. Like 0036 / 0060 / 0072, this revision exists only to give Alembic one head again.

Two lineages branch off `0172_instruction_fee_type`:

    0172 -> 0173_sipp_illustration -> … -> 0177_products_client_read -> 0178_seed_book_job
    0172 -> 0173_transfer_pension_facts                                (the original id)

The transfer-facts work was applied locally as 0173, then committed as 0176 after 0173–0175
had already taken those numbers on main. Databases stamped with the original id cannot see
0173_sipp / 0174 / 0175 / 0177 / 0178_seed_book_job until this merge; databases already on
0178_seed_book_job pick up the stub as a no-op; later revisions (the 0179–0186 stubs, then
`0196_merge_sipp_drawdown` and `0205_merge_sipp_drawdown`) continue from here.

**Why a merge and not a stamp or a re-point.** Stamping the orphaned database to 0176 would
skip 0173_sipp–0175 (those columns are not on it). Re-pointing 0176's parent onto the old id
rewrites a revision that has already been applied on the linear path. A merge touches
nothing that exists: both ids stay as they were applied, and this is the first revision that
requires both.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would restore two heads.
"""

from collections.abc import Sequence

revision: str = "0178_merge_transfer_facts"
down_revision: str | Sequence[str] | None = (
    "0178_seed_book_job",
    "0173_transfer_pension_facts",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
