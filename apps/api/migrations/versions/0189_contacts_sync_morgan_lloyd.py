"""Keep the 0189 contacts-sync revision id locatable

Revision ID: 0189_contacts_sync_morgan_lloyd
Revises: 0188_outbound_transactions
Create Date: 2026-09-21

NO DDL. The grant backfill lives in `0198_contacts_sync_morgan_lloyd`.
"""

revision: str = "0189_contacts_sync_morgan_lloyd"
down_revision: str | None = "0188_outbound_transactions"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0199 carries the grants."""


def downgrade() -> None:
    """Nothing to undo. The grants belong to 0199."""
