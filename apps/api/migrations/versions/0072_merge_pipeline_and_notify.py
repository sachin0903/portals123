"""Join the provider-pipeline lineage to the notifications line

Revision ID: 0072_merge_pipeline_and_notify
Revises: 0070_merge_linked_exclude, 0071_drop_notification_seen_at
Create Date: 2026-08-08

NO DDL. Like 0060, 0061 and 0069, this revision exists only to give alembic one head again.

`notify` rather than `notifications` because **`alembic_version.version_num` is `varchar(32)`** and
the fuller name is 37 characters. It does not fail at import or at plan — it fails on the UPDATE that
stamps the row, after the migrations have run, as
`value too long for type character varying(32)`. The whole upgrade then rolls back, so the symptom is
a chain that will not apply and a name that looks fine.

Both lineages branch off `0062_email_version_immutable` and neither knew about the other:

    0062 -> 0063_notifications -> … -> 0071_drop_notification_seen_at    (notifications)
    0062 -> 0069_merge_pipeline_and_email -> 0070_merge_linked_exclude   (the provider pipeline)

The numbers collide across 0063-0070, which is cosmetic in exactly the way 0061's and 0069's notes
describe: each revision names its own parent, the file names differ, and the CHAINS are disjoint —
the notifications side adds `notifications.*` and its RLS, the pipeline side adds `inbound`,
`storage.tenant_service_users` and the `platform.merge_*` functions. Nothing on either side
references the other's objects. The two HEADS are what breaks `alembic upgrade head`, and that is
all this fixes.

**Why a merge and not a renumber or a re-point.** Renumbering my side to 0072+ would rewrite files
whose revision ids are already stamped in every database that has applied them, and re-pointing is
the thing one reaches for first and should not: 0061's author tried it and reverted it, because it
rewrites the declared parentage of revisions that have ALREADY been applied, so the stamped history
disagrees with the files and a later `downgrade` walks a path that never happened. A merge revision
touches nothing that exists — both lineages stay exactly as they were applied, and this is simply the
first revision that requires both.

Ordering between the two sides is deliberately UNCONSTRAINED, which is safe because they are
disjoint. A future migration needing one to precede the other declares that itself by naming the
right parent.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would re-create the two heads this
exists to remove.
"""

from collections.abc import Sequence

revision: str = "0072_merge_pipeline_and_notify"
down_revision: str | Sequence[str] | None = (
    # Each lineage's head. They move with their lineage rather than staying pinned, for the reason
    # 0060's parent comment gives: a later child would otherwise re-create two heads.
    "0070_merge_linked_exclude",
    "0071_drop_notification_seen_at",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
