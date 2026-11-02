"""ongoing charge on an instrument

Revision ID: 0217_instrument_ocf
Revises: 0216_action_approvers
Create Date: 2026-10-04

``alembic_version.version_num`` is ``varchar(32)``; this id is 18 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0217_instrument_ocf"
down_revision: str | None = "0216_action_approvers"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0217_instrument_ocf.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE platform.instruments DROP COLUMN IF EXISTS ocf;
        """
    )
