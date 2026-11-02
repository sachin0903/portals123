"""Seccl and SEI ids on a transfer

Revision ID: 0188_transfer_provider_ids
Revises: 0187_provider_columns
Create Date: 2026-09-26

`alembic_version.version_num` is varchar(32); this id fits.
"""

from pathlib import Path

from alembic import op

revision: str = "0188_transfer_provider_ids"
down_revision: str | None = "0187_provider_columns"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0188_transfer_provider_ids.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE platform.transfers
          DROP COLUMN IF EXISTS seccl_transfer_id,
          DROP COLUMN IF EXISTS sei_activity_id;
        """
    )
