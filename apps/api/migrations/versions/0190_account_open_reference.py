"""Keep the 0190 account-open-reference revision id locatable

Revision ID: 0190_account_open_reference
Revises: 0189_contacts_sync_morgan_lloyd
Create Date: 2026-09-22

NO DDL. The column lives in `0199_account_open_reference`.
"""

revision: str = "0190_account_open_reference"
down_revision: str | None = "0189_contacts_sync_morgan_lloyd"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0200 carries the column."""


def downgrade() -> None:
    """Nothing to undo. The column belongs to 0200."""
