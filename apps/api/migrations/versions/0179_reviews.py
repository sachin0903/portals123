"""reviews: four-eye checks (peer review)

Revision ID: 0179_reviews
Revises: 0178_seed_book_job
Create Date: 2026-09-22
"""

from pathlib import Path

from alembic import op

revision: str = "0179_reviews"
down_revision: str | None = "0178_seed_book_job"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0179_reviews.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS reviews.requests;
        DROP TABLE IF EXISTS reviews.policies;
        DROP SCHEMA IF EXISTS reviews;
        """
    )
