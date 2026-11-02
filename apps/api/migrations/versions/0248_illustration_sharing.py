"""Illustrations are shared with the client by a person, unless the firm says otherwise.

Revision ID: 0248_illustration_sharing
Revises: 0247_merge_integrations
Create Date: 2026-10-11

Every illustration rendered was filed under its client at once — the 'subject' link that is the
client's read permission — so a draft an adviser had not looked at was already in the portal
(FB-20261006-04). The flag is per firm and OFF by default: an illustration is held until staff
share it, and a firm that wants the old behaviour switches it on.
"""

from alembic import op

revision: str = "0248_illustration_sharing"
down_revision: str | None = "0247_merge_integrations"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE auth.tenants "
        "ADD COLUMN illustrations_auto_share boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "COMMENT ON COLUMN auth.tenants.illustrations_auto_share IS "
        "'Show a rendered illustration in the client portal at once. Off: staff share each one.'"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE auth.tenants DROP COLUMN IF EXISTS illustrations_auto_share")
