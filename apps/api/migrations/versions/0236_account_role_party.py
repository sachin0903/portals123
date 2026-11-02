"""Keep the original account-role revision id locatable

Revision ID: 0236_account_role_party
Revises: 0234_txn_public_id
Create Date: 2026-10-07

NO DDL. ``party`` on ``crm.account_role`` lives in ``0243_account_role_party``; this file
exists only so Alembic can still resolve the id this revision was first applied under.

The work was written as 0236, applied to a local database, then committed as 0243 after
0235–0242 had taken those numbers on main. Alembic records the id, not the filename, so
``upgrade head`` on that database fails with ``Can't locate revision identified by
'0236_account_role_party'`` until this id is back in the graph. 0243 uses
``ADD VALUE IF NOT EXISTS``, and 0242 uses ``ADD COLUMN IF NOT EXISTS``, so a database
that already ran the original 0236 can apply both without colliding.

``0244_merge_account_party`` joins this stub to the 0235 → 0243 line. Do not re-point
0243's parent onto this id: 0243 has already been applied on the linear path.
"""

revision: str = "0236_account_role_party"
down_revision: str | None = "0234_txn_public_id"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0243 carries the enum value, including for databases that skipped this id."""


def downgrade() -> None:
    """Nothing to undo. The enum value belongs to 0243."""
