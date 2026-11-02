"""join the platforms and audit-remediation migration lines

Revision ID: 0036_merge_platforms_audit
Revises: 0035_link_tenant_integrity, 0029_tenant_providers
Create Date: 2026-07-28

A no-op merge revision, not a schema change. Two lines branched off
0029_dataset_sample_data and were developed in parallel:

  * the platforms line   -> 0027_tenant_storage -> 0028_storage_credentials
                            -> 0029_tenant_providers
  * the remediation line -> 0030_auth_directory_scope .. 0035_link_tenant_integrity

(The platforms line reuses the filename numbers 0027-0029; the chain in each file is what
orders them, not the filename. Do not "fix" the numbering — the revision ids are applied.)

Merging rather than renumbering is deliberate: both lines have already been applied to real
databases, so rewriting either one's revision ids or down_revision would leave those databases
with an alembic_version pointing at a revision that no longer exists. A merge revision unifies
the heads while leaving every existing id untouched, so any database on either head upgrades
forward cleanly.
"""

# revision identifiers, used by Alembic.
revision: str = "0036_merge_platforms_audit"
down_revision: tuple[str, ...] | None = ("0035_link_tenant_integrity", "0029_tenant_providers")
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do: this revision exists only to join the two lines."""


def downgrade() -> None:
    """Nothing to undo — downgrading past this point re-forks the two heads."""
