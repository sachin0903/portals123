"""fill a platform's wrapper reference so the shelf stops showing a bare "—"

Revision ID: 0267_product_wrapper_ref
Revises: 0266_action_policy_manual
Create Date: 2026-10-20

product_platforms.external_id is the custodian's / administrator's own reference for the product.
It was left null until the nightly merge discovered one from an existing account, so a wrapper the
firm offers but has no account for yet showed its enabled platform with no reference. The reference
does not vary between firms, so this backfills every null from the product's wrapper type; the
attach functions stamp the same default for rows created after this. The SQL file says why.
"""

from pathlib import Path

from alembic import op

revision: str = "0267_product_wrapper_ref"
down_revision: str | None = "0266_action_policy_manual"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0267_product_wrapper_ref.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """The reference is the wrapper's own name; there is nothing to undo and no earlier value to
    restore (the column was null). Leave the shelf populated."""
