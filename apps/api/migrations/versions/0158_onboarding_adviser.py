"""Backfill onboarding's chosen adviser onto client records

Revision ID: 0158_onboarding_adviser
Revises: 0157_gr_identifier_english
Create Date: 2026-09-13
"""

from pathlib import Path

from alembic import op

revision: str = "0158_onboarding_adviser"
down_revision: str | None = "0157_gr_identifier_english"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0158_onboarding_adviser.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The inserted relationship is indistinguishable from one subsequently affirmed by a firm.
    pass
