"""Join the flows and platform migration lineages after the rebase onto main

Revision ID: 0060_merge_flows_and_platform
Revises: 0050_flow_ai, 0059_product_names
Create Date: 2026-08-04

NO DDL. This revision exists only to give alembic one head again.

The two lineages forked at 0043 and ran in parallel for sixteen revisions:

    0043_automations   -> 0044_tags -> 0045_flow_timers -> 0046_case_memory
                       -> 0047_flow_analytics -> 0048_automation_platform_pause
                       -> 0049_delivery_settled_windows -> 0050_flow_ai
                                                                 (main: the flows engine)

    0043_links_rename  -> 0044_products_multi_platform -> 0045_tenant_usage
                       -> 0046_transfer_provider_rails -> 0047_api_log
                       -> 0048_providers_into_platform -> ... -> 0059_product_names
                                                                  (this branch: the platform)

So the numbers collide (two 0043s, two 0044s, ..., two 0050s) but the CHAINS are disjoint:
each revision names its own parent, and no revision on one side references the other. The
collision is cosmetic; the two heads are what broke `alembic upgrade head`.

**Why a merge and not a re-point.** The obvious alternative is to change one lineage's root
`down_revision` to sit after the other's head, making a single line. That rewrites the declared
parentage of revisions that have ALREADY BEEN APPLIED -- on dev, on staging, and on any
environment tracking either branch. Alembic records only the current revision in
`alembic_version`, so a re-pointed ancestor is not re-run, but the stamped history then
disagrees with the files, and a later `downgrade` walks a path that never happened. A merge
revision touches nothing that exists: both lineages stay exactly as they were applied, and this
is simply the first revision that requires both.

Ordering between the two sides is therefore deliberately UNCONSTRAINED, which is safe here
because they are disjoint: flows adds `flows.*`, this branch adds `platform.*`/`crm.*`, and
neither references the other's objects. If a future migration needs one to precede the other,
it declares that itself by naming the right parent.

`downgrade` is a no-op for the same reason `upgrade` is: unmerging would re-create the two
heads, which is the state this exists to remove.
"""

from collections.abc import Sequence

revision: str = "0060_merge_flows_and_platform"
down_revision: str | Sequence[str] | None = (
    # The flows head. It was 0049_delivery_settled_windows when this was written; the rebase onto
    # main brought 0050_flow_ai in as its child, which re-created the two heads this exists to
    # remove — so the parent moves with the lineage's head rather than staying at a fixed revision.
    "0050_flow_ai",
    "0059_product_names",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
