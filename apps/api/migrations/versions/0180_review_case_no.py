"""reviews: per-firm sequential case number

Revision ID: 0180_review_case_no
Revises: 0179_reviews
Create Date: 2026-09-23
"""

from pathlib import Path

from alembic import op

revision: str = "0180_review_case_no"
down_revision: str | None = "0179_reviews"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0180_review_case_no.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP INDEX IF EXISTS reviews.reviews_requests_case_no;
        ALTER TABLE reviews.requests DROP COLUMN IF EXISTS case_no;
        """
    )
