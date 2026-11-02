"""Keep the original transfer-facts revision id locatable

Revision ID: 0173_transfer_pension_facts
Revises: 0172_instruction_fee_type
Create Date: 2026-09-16

NO DDL. The pension-transfer columns live in `0176_transfer_pension_facts`; this file exists
only so Alembic can still resolve the id this revision was first applied under.

The work was written as 0173, applied to at least one real database, then renamed to 0176 when
0173–0175 landed on main in the same window. Alembic records the id, not the filename, so
`upgrade head` on those databases fails with `Can't locate revision identified by
'0173_transfer_pension_facts'` until this id is back in the graph. The SQL is not here —
0176 uses `ADD COLUMN IF NOT EXISTS`, so a database that already ran the original 0173 can
apply 0176 without colliding, and a fresh database gets the columns once from 0176.

`0178_merge_transfer_facts` joins this stub to the 0173_sipp → 0178_seed_book_job line. Do not re-point
0176's parent onto this id: 0176 has already been applied on the linear path.
"""

revision: str = "0173_transfer_pension_facts"
down_revision: str | None = "0172_instruction_fee_type"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0176 carries the DDL, including for databases that skipped this id."""


def downgrade() -> None:
    """Nothing to undo. The columns belong to 0176."""
