"""Join the provider-pipeline lineage to the email-template line

Revision ID: 0069_merge_pipeline_and_email
Revises: 0062_email_version_immutable, 0068_merge_model_versions
Create Date: 2026-08-06

NO DDL. Like 0060 and 0061, this revision exists only to give alembic one head again.

Both lineages branch off `0060_merge_flows_and_platform` and neither knew about the other:

    0060 -> 0061_merge_email_authorship -> 0062_email_version_immutable        (email templates)
    0060 -> 0061_tenant_service_users -> … -> 0068_merge_model_versions        (the provider pipeline)

The numbers collide on both 0061 and 0062, which is cosmetic in exactly the way 0061's own note
describes: each revision names its own parent, the file names differ, and the CHAINS are disjoint —
the email side adds `email.*` and the pipeline side adds `inbound`, `storage.tenant_service_users`
and the `platform.merge_*` functions. Nothing on either side references the other's objects. The two
HEADS are what breaks `alembic upgrade head`, and that is all this fixes.

**Why a merge and not a re-point**, restated because it is the thing one reaches for first: 0061's
author tried re-pointing and reverted it. Re-pointing rewrites the declared parentage of revisions
that have ALREADY been applied, so the stamped history disagrees with the files and a later
`downgrade` walks a path that never happened. A merge revision touches nothing that exists — both
lineages stay exactly as they were applied, and this is simply the first revision that requires both.

Ordering between the two sides is deliberately UNCONSTRAINED, which is safe because they are
disjoint. A future migration needing one to precede the other declares that itself by naming the
right parent.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would re-create the two heads this
exists to remove.
"""

from collections.abc import Sequence

revision: str = "0069_merge_pipeline_and_email"
down_revision: str | Sequence[str] | None = (
    # Each lineage's head. They move with their lineage rather than staying pinned, for the reason
    # 0060's parent comment gives: a later child would otherwise re-create two heads.
    "0062_email_version_immutable",
    "0068_merge_model_versions",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
