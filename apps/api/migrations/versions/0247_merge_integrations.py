"""Join the uncommitted integrations-tenant id to the linear 0245–0246 line

Revision ID: 0247_merge_integrations
Revises: 0246_contact_name_from_parts, 0246_integrations_tenant
Create Date: 2026-10-11

NO DDL. Like 0244, this revision exists only to give Alembic one head again.

Two lineages branch off ``0244_merge_account_party``:

    0244 -> 0245_one_adviser_per_client -> 0246_contact_name_from_parts
    0244 -> 0246_integrations_tenant

``0246_integrations_tenant`` was applied to dev from a revision that was never
committed, so that database cannot see 0245 or 0246 until this merge. A
database already on 0246 picks the stub up as a no-op.

**Why a merge and not a stamp.** Stamping dev back to 0244 would drop the
record of objects that migration created (``integrations.transactions_sent``,
the retyped ``platforms.firm_id``). Those stay as they are; the revision that
describes them belongs to the repo that owns them, and it must come after this
merge and be safe to run twice.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would
restore two heads.

``alembic_version.version_num`` is ``varchar(32)``; this id is 23 characters.
"""

from collections.abc import Sequence

revision: str = "0247_merge_integrations"
down_revision: str | Sequence[str] | None = (
    "0246_contact_name_from_parts",
    "0246_integrations_tenant",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
