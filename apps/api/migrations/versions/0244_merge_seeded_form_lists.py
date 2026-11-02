"""Join the renamed seeded-form-lists id to the linear 0235–0243 line

Revision ID: 0244_merge_seeded_form_lists
Revises: 0243_terms_second_wave, 0237_seeded_form_lists
Create Date: 2026-10-08

NO DDL. Like 0178_merge_transfer_facts, this revision exists only to give Alembic one head.

Two lineages branch off `0234_txn_public_id`:

    0234 -> 0235_identity_photos -> … -> 0241_seeded_form_lists -> 0242 -> 0243
    0234 -> 0237_seeded_form_lists                                      (the original id)

The closed-lists / mobile / fact-find work was applied locally as 0235–0237, then committed
as 0239–0241 after 0235–0238 had already taken those numbers on main. Databases stamped with
the original id cannot see the identity-photo and named-portal revisions until this merge;
databases already on 0243 pick up the stub as a no-op.

**Why a merge and not a stamp or a re-point.** Stamping the orphaned database to 0241 would
skip 0235_identity_photos–0238 (those objects are not on it). Re-pointing 0241's parent onto
the old id rewrites a revision that has already been applied on the linear path. A merge
touches nothing that exists: both ids stay as they were applied, and this is the first
revision that requires both.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would restore two heads.
"""

from collections.abc import Sequence

revision: str = "0244_merge_seeded_form_lists"
down_revision: str | Sequence[str] | None = (
    "0243_terms_second_wave",
    "0237_seeded_form_lists",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
