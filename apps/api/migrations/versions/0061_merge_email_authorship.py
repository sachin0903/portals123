"""Join the email-template lineage to the merged flows/platform line

Revision ID: 0061_merge_email_authorship
Revises: 0051_email_version_authorship, 0060_merge_flows_and_platform
Create Date: 2026-08-04

NO DDL. Like 0060, this revision exists only to give alembic one head again.

`0042_email_templates` forked off and ran on its own, gaining one child:

    0042_email_templates -> 0051_email_version_authorship   (the email-template store)
    ... -> 0050_flow_ai / 0059_product_names -> 0060_merge_flows_and_platform   (everything else)

The numbers collide (there is also a 0051_contributions_ledger on the other side) but the CHAINS
are disjoint: each revision names its own parent and neither side references the other's objects.
The collision is cosmetic; the two heads are what broke `alembic upgrade head`.

**Why a merge and not a re-point** — the same reasoning 0060 sets out, and it applies here too.
Re-pointing `0051_email_version_authorship` to sit after `0060` was tried first and reverted: it
rewrites the declared parentage of a revision that has already been applied, so the stamped history
disagrees with the files and a later `downgrade` walks a path that never happened. A merge revision
touches nothing that exists — both lineages stay exactly as they were applied, and this is simply
the first revision that requires both.

Ordering between the two sides is deliberately UNCONSTRAINED, which is safe because they are
disjoint: the email side adds `email.*` only, and nothing on the other side references it. A future
migration needing one to precede the other declares that itself by naming the right parent.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would re-create the two heads
this exists to remove.
"""

from collections.abc import Sequence

revision: str = "0061_merge_email_authorship"
down_revision: str | Sequence[str] | None = (
    # The email-template lineage's head. It moves with that lineage rather than staying pinned, for
    # the reason 0060's parent comment gives: a later child would otherwise re-create two heads.
    "0051_email_version_authorship",
    "0060_merge_flows_and_platform",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
