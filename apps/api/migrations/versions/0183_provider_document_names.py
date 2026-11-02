"""Keep the original provider-document-names revision id locatable

Revision ID: 0183_provider_document_names
Revises: 0182_provider_documents
Create Date: 2026-09-22

NO DDL. The function body lives in `0201_provider_document_names`.
"""

revision: str = "0183_provider_document_names"
down_revision: str | None = "0182_provider_documents"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0202 carries the function."""


def downgrade() -> None:
    """Nothing to undo. The function belongs to 0202."""
