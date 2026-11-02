"""Fold peer review into the action registry

Revision ID: 0249_fold_reviews
Revises: 0248_txn_review_status
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision: str = "0249_fold_reviews"
down_revision: str | None = "0248_txn_review_status"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0249_fold_reviews.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("0249 folds reviews into actions; restore from a backup")
