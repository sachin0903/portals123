"""Per-action switch for a justification on a held change

Revision ID: 0250_require_justification
Revises: 0249_fold_reviews
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision: str = "0250_require_justification"
down_revision: str | None = "0249_fold_reviews"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0250_require_justification.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE actions.policies DROP COLUMN IF EXISTS require_justification;
        """
    )
