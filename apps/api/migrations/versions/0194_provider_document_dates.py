"""Keep the 0194 provider-document-dates revision id locatable

Revision ID: 0194_provider_document_dates
Revises: 0193_sipp_scheme_admin
Create Date: 2026-09-23

NO DDL. The function body lives in `0203_provider_document_dates`.
"""

revision: str = "0194_provider_document_dates"
down_revision: str | None = "0193_sipp_scheme_admin"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0204 carries the function."""


def downgrade() -> None:
    """Nothing to undo. The function belongs to 0204."""
