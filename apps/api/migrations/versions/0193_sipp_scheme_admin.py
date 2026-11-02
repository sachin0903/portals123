"""Keep the 0193 SIPP-scheme-admin revision id locatable

Revision ID: 0193_sipp_scheme_admin
Revises: 0192_provider_document_names
Create Date: 2026-09-23

NO DDL. The mapping cleanup lives in `0202_sipp_scheme_admin`.
"""

revision: str = "0193_sipp_scheme_admin"
down_revision: str | None = "0192_provider_document_names"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0203 carries the DELETE."""


def downgrade() -> None:
    """Nothing to undo. The cleanup belongs to 0203."""
