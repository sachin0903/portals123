"""Keep the original outbound-transactions revision id locatable

Revision ID: 0179_outbound_transactions
Revises: 0187_provider_columns, 0178_merge_transfer_facts
Create Date: 2026-09-21

NO DDL. The pending-transaction columns live in `0197_outbound_transactions`;
this file exists only so Alembic can still resolve the id this revision was
first applied under.

The work was written as 0179, applied to local databases, then moved to 0188
when main took 0179–0187, then to 0198 when main took 0188–0191. Alembic
records the id, not the filename, so `upgrade head` on those databases fails
with `Can't locate revision identified by '0179_outbound_transactions'` until
this id is back in the graph.

Parents stay `0187_provider_columns` and `0178_merge_transfer_facts` so
already-applied parentage is not rewritten — 0060 / 0072. The SQL is not here:
0198 uses `IF NOT EXISTS`, so a database that already ran 0179 or 0188 can
apply 0198 without colliding, and a fresh database gets the objects once.

`0196_merge_sipp_drawdown` joins this stub line to the 0188–0195 stubs.
`0205_merge_sipp_drawdown` joins that to the DDL. Do not re-point 0188's parent
onto this id: 0188 has already been applied on the linear path.
"""

from collections.abc import Sequence

revision: str = "0179_outbound_transactions"
down_revision: str | Sequence[str] | None = (
    "0187_provider_columns",
    "0178_merge_transfer_facts",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0198 carries the DDL, including for databases that skipped this id."""


def downgrade() -> None:
    """Nothing to undo. The objects belong to 0198."""
