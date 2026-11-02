"""Keep the original provider-documents revision id locatable

Revision ID: 0182_provider_documents
Revises: 0181_account_open_reference
Create Date: 2026-09-22

NO DDL. The ingest function lives in `0200_provider_documents`.
"""

revision: str = "0182_provider_documents"
down_revision: str | None = "0181_account_open_reference"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0201 carries the ingest function."""


def downgrade() -> None:
    """Nothing to undo. The objects belong to 0201."""
