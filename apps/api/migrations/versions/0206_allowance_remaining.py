"""ISA/JISA remaining allowance, stated separately from the limit

Revision ID: 0206_allowance_remaining
Revises: 0205_merge_sipp_drawdown
Create Date: 2026-09-29

A remaining figure can exist when the annual limit cannot be derived. Storing it
in ``allowance_limit`` would claim that whatever is left is the whole allowance.
"""

from pathlib import Path

from alembic import op

revision: str = "0206_allowance_remaining"
down_revision: str | None = "0205_merge_sipp_drawdown"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0206_allowance_remaining.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.accounts DROP COLUMN IF EXISTS allowance_remaining"
    )
