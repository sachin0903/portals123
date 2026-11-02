"""Join the transfer-provider lineage to the illustration merge

Revision ID: 0191_merge_transfers
Revises: 0190_merge_pay_and_illus, 0189_transfer_adviser_note
Create Date: 2026-09-28

NO DDL. The transfer provider ids and adviser note branched off ``0187_provider_columns``
while main joined the payment-method and illustration lineages at ``0190_merge_pay_and_illus``.
They touch different tables, so the order between them does not matter. This is the first
revision that requires both.

``alembic_version.version_num`` is ``varchar(32)``; this id is 21 characters.
"""

from collections.abc import Sequence

revision: str = "0191_merge_transfers"
down_revision: str | Sequence[str] | None = (
    "0190_merge_pay_and_illus",
    "0189_transfer_adviser_note",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
