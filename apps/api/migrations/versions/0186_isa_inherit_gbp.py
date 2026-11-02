"""Keep the original ISA-inherit-GBP revision id locatable

Revision ID: 0186_isa_inherit_gbp
Revises: 0185_provider_document_dates
Create Date: 2026-09-23

NO DDL. The trigger body lives in `0204_isa_inherit_gbp`.
`0196_merge_sipp_drawdown` joins this stub to the 0188–0195 stubs.
"""

revision: str = "0186_isa_inherit_gbp"
down_revision: str | None = "0185_provider_document_dates"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0205 carries the trigger."""


def downgrade() -> None:
    """Nothing to undo. The trigger belongs to 0205."""
