"""Join the renamed account-role id to the linear 0235–0243 line

Revision ID: 0244_merge_account_party
Revises: 0243_account_role_party, 0236_account_role_party
Create Date: 2026-10-09

NO DDL. Like 0178, this revision exists only to give Alembic one head again.

Two lineages branch off ``0234_txn_public_id``:

    0234 -> 0235_identity_photos -> … -> 0243_account_role_party
    0234 -> 0236_account_role_party

The party role was applied locally as 0236, then committed as 0243 after 0235–0242 had
already taken those numbers. Databases stamped with the original id cannot see 0235–0242
until this merge; databases already on 0243 pick up the stub as a no-op.

**Why a merge and not a stamp.** Stamping the orphaned database to 0243 would skip
0235–0241 (those objects are not on it). A merge touches nothing that exists: both ids
stay as they were applied, and this is the first revision that requires both.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would restore two heads.
"""

from collections.abc import Sequence

revision: str = "0244_merge_account_party"
down_revision: str | Sequence[str] | None = (
    "0243_account_role_party",
    "0236_account_role_party",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
