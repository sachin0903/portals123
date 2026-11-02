"""platform.transfers: basis (cash/in-specie) and extent (full/partial) as columns

Revision ID: 0148_transfer_basis
Revises: 0147_contact_notes_audit
Create Date: 2026-09-10
"""

from pathlib import Path

from alembic import op

revision: str = "0148_transfer_basis"
down_revision: str | None = "0147_contact_notes_audit"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0148_transfer_basis.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.transfers "
        "DROP CONSTRAINT IF EXISTS transfers_basis_check, "
        "DROP CONSTRAINT IF EXISTS transfers_extent_check, "
        "DROP COLUMN IF EXISTS basis, "
        "DROP COLUMN IF EXISTS extent;"
    )
