"""Join the withdrawal-rail lineage to the GIA/ISA illustration lineage

Revision ID: 0190_merge_pay_and_illus
Revises: 0189_instruction_payment_method, 0189_gia_isa_illustration
Create Date: 2026-09-28

NO DDL. Both revisions branch off ``0188_journey_assignee`` and neither knew about the
other: the illustration one landed on main and was applied by deploy, then the payment
method one was rebased beside it. ``alembic upgrade head`` refuses two heads, which is
what failed the deploy. They touch different tables, so the order between them does not
matter. This is the first revision that requires both.

``alembic_version.version_num`` is ``varchar(32)``; this id is 24 characters.
"""

from collections.abc import Sequence

revision: str = "0190_merge_pay_and_illus"
down_revision: str | Sequence[str] | None = (
    "0189_instruction_payment_method",
    "0189_gia_isa_illustration",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
