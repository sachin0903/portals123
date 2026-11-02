"""Adviser note on a synced transfer

Revision ID: 0189_transfer_adviser_note
Revises: 0188_transfer_provider_ids
Create Date: 2026-09-27

`alembic_version.version_num` is varchar(32); this id fits.
"""

from pathlib import Path

from alembic import op

revision: str = "0189_transfer_adviser_note"
down_revision: str | None = "0188_transfer_provider_ids"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0189_transfer_adviser_note.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE platform.transactions DROP COLUMN IF EXISTS adviser_note;
        """
    )
